"""Orgstra S001 (Xinye "YK" SDK) Bluetooth-SPP thermal label printer.

Reverse-engineered from the "Snap & Tag" app (com.xinye.snaptag) and verified
byte-for-byte against the native builder `getPrintLabelWithTypeAndPaperCommand`.

Wire framing (see :func:`_frame`)::

    0x64 | CMD | SEQ | LEN(2 LE) | PAYLOAD | CHECKSUM(4, always 0) | 0x9B

A print job is a fixed sequence of small setup frames, the raster split into
``CMD_RASTER`` chunks, then a print trigger. The image is stored column-major and
x-mirrored (the app rotates 270 deg); each column is 12 bytes = the fixed 96-dot
(12 mm) head, rows packed MSB-first after a 6-bit top pad. This matches the native
builder byte-for-byte and prints a full clean label (see re/pc_print.py).
"""
from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior

# ---------------------------------------------------------------------------
# Wire framing
# ---------------------------------------------------------------------------
FRAME_START = 0x64
FRAME_END = 0x9B
FRAME_CHECKSUM = b"\x00\x00\x00\x00"   # 4-byte checksum field, always 0 in captures

# Command ids
CMD_SET_SPEED = 0x09       # payload: [speed]
CMD_SET_WIDTH = 0x0A       # payload: [width_param]
CMD_SET_FEED = 0x02        # payload: feed length, 2 bytes LE (dots)
CMD_LABEL = 0x03           # label config / print trigger (payload[0] selects)
CMD_SET_PAPER_TYPE = 0x28  # payload: [0x01, paper_type]
CMD_RASTER = 0x00          # payload: raster chunk

# CMD_LABEL payload = [op] + LABEL_CONFIG_VALUE(2 LE); op distinguishes the two uses
LABEL_OP_CONFIG = 0x02     # setup frame before the raster
LABEL_CONFIG_VALUE = 800   # 0x0320, constant across all captures

# ---------------------------------------------------------------------------
# Raster geometry
# ---------------------------------------------------------------------------
HEAD_DOTS = 96                     # 12 mm head @ 8 dot/mm (native format, verified full print)
BYTES_PER_COL = HEAD_DOTS // 8     # 12
TOP_PAD_BITS = 6                   # image sits 6 dots below the top of the column (native)
RASTER_COLS_PER_CHUNK = 24         # native app sent 24 whole columns per CMD_RASTER frame
RASTER_CHUNK = RASTER_COLS_PER_CHUNK * BYTES_PER_COL  # column-aligned payload size (= 288)
DOTS_PER_MM = 8

# Paper-type byte in the CMD_SET_PAPER_TYPE frame
PAPER_TYPE_CONTINUOUS = 0
PAPER_TYPE_GAP = 1
PAPER_TYPE_MARK = 2
_PAPER_TYPE_CODE = {
    PaperMode.TAG: PAPER_TYPE_GAP,
    PaperMode.BLACK_TAG: PAPER_TYPE_MARK,
    PaperMode.PLAIN: PAPER_TYPE_CONTINUOUS,
}

# Defaults observed from the real app (S001_GAP label)
DEFAULT_WIDTH_PARAM = 25
DEFAULT_SPEED_PARAM = 9
DEFAULT_BOTTOM_MARGIN_MM = 2.5     # 'f4', used by the trailing-feed formula


class _Seq:
    """Per-frame sequence counter (one byte, wraps at 256)."""

    def __init__(self, start: int = 0) -> None:
        self.value = start & 0xFF

    def take(self) -> int:
        current = self.value
        self.value = (self.value + 1) & 0xFF
        return current


def _frame(cmd: int, payload: bytes, seq: _Seq) -> bytes:
    """Wrap a command + payload in the S001 frame envelope."""
    length = len(payload)
    return (
        bytes((FRAME_START, cmd, seq.take(), length & 0xFF, (length >> 8) & 0xFF))
        + payload
        + FRAME_CHECKSUM
        + bytes((FRAME_END,))
    )


# ---------------------------------------------------------------------------
# Individual frame builders (reusable)
# ---------------------------------------------------------------------------
def set_width_frame(width_param: int, seq: _Seq) -> bytes:
    return _frame(CMD_SET_WIDTH, bytes((width_param & 0xFF,)), seq)


def set_speed_frame(speed: int, seq: _Seq) -> bytes:
    return _frame(CMD_SET_SPEED, bytes((speed & 0xFF,)), seq)


def set_paper_type_frame(paper_type: int, seq: _Seq) -> bytes:
    return _frame(CMD_SET_PAPER_TYPE, bytes((0x01, paper_type & 0xFF)), seq)


def label_config_frame(seq: _Seq) -> bytes:
    return _frame(CMD_LABEL, _label_payload(LABEL_OP_CONFIG), seq)


def set_feed_frame(feed_dots: int, seq: _Seq) -> bytes:
    return _frame(CMD_SET_FEED, bytes((feed_dots & 0xFF, (feed_dots >> 8) & 0xFF)), seq)


def raster_frame(chunk: bytes, seq: _Seq) -> bytes:
    return _frame(CMD_RASTER, chunk, seq)


def print_trigger_frame(seq: _Seq, feed_after: bool = True) -> bytes:
    """Final frame that commits the label. payload[0] is the app's 'z2' flag."""
    return _frame(CMD_LABEL, _label_payload(0x01 if feed_after else 0x00), seq)


def _label_payload(op: int) -> bytes:
    return bytes((op,)) + bytes((LABEL_CONFIG_VALUE & 0xFF, (LABEL_CONFIG_VALUE >> 8) & 0xFF))


# ---------------------------------------------------------------------------
# Raster packing
# ---------------------------------------------------------------------------
def pack_raster(pixels, head_width: int, column_count: int) -> bytes:
    """Pack a BW1 image (row-major, 1=black) into the column-major S001 buffer.

    Uses TiMini's raster orientation: ``head_width`` runs across the print head
    (only the first ``HEAD_DOTS - TOP_PAD_BITS`` = 90 dots are used), and
    ``column_count`` (the raster height / number of rows) is the number of
    along-feed columns. Each printed column is ``BYTES_PER_COL`` bytes; columns
    are stored x-mirrored, head dots packed MSB-first after a ``TOP_PAD_BITS``
    top pad.
    """
    heads = min(head_width, HEAD_DOTS - TOP_PAD_BITS)   # image sits below the top pad
    raster = bytearray(column_count * BYTES_PER_COL)
    for col in range(column_count):
        column_base = (column_count - 1 - col) * BYTES_PER_COL   # x-mirrored
        row = col * head_width
        for head in range(heads):
            if pixels[row + head]:
                bit = TOP_PAD_BITS + head
                raster[column_base + (bit >> 3)] |= 0x80 >> (bit & 7)
    return bytes(raster)


def raster_data_frames(pixels, head_width: int, column_count: int, seq: _Seq) -> bytes:
    data = pack_raster(pixels, head_width, column_count)
    out = bytearray()
    for offset in range(0, len(data), RASTER_CHUNK):
        out += raster_frame(data[offset:offset + RASTER_CHUNK], seq)
    return bytes(out)


# ---------------------------------------------------------------------------
# Job assembly
# ---------------------------------------------------------------------------
def _paper_type_code(request: PrintJobRequest) -> int:
    return _PAPER_TYPE_CODE.get(request.paper_mode, PAPER_TYPE_GAP)


def _label_length_dots(request: PrintJobRequest, column_count: int) -> int:
    """Physical label length in dots (== number of feed columns); falls back to
    the image length when the profile carries no explicit length (``one_length``)."""
    if request.one_length and request.one_length > 0:
        return request.one_length
    return column_count


def _trailing_feed_dots(label_length_dots: int, column_count: int, bottom_margin_mm: float) -> int:
    """Dots to feed after the image so the label ends past its trailing edge."""
    return round(label_length_dots) - column_count - round(bottom_margin_mm * DOTS_PER_MM) + 1


def _build_s001_job(request: PrintJobRequest) -> ProtocolPlan:
    raster = request.require_raster(PixelFormat.BW1)
    head_width = raster.width          # across the print head (<= 96)
    column_count = raster.height       # along-feed columns (label length in dots)
    pixels = list(raster.pixels)
    speed = request.speed if request.speed is not None else DEFAULT_SPEED_PARAM

    seq = _Seq()
    out = bytearray()
    out += set_width_frame(DEFAULT_WIDTH_PARAM, seq)
    out += set_speed_frame(speed, seq)
    out += set_paper_type_frame(_paper_type_code(request), seq)
    out += label_config_frame(seq)

    feed = _trailing_feed_dots(
        _label_length_dots(request, column_count), column_count, DEFAULT_BOTTOM_MARGIN_MM
    )
    if feed > 0:
        out += set_feed_frame(feed, seq)

    out += raster_data_frames(pixels, head_width, column_count, seq)
    out += print_trigger_frame(seq)
    return ProtocolPlan.stream(bytes(out))


BEHAVIOR = ProtocolBehavior(
    requires_speed=False,
    supported_paper_modes=(PaperMode.TAG, PaperMode.BLACK_TAG, PaperMode.PLAIN),
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.YK_S001_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.YK_S001_RASTER: (PixelFormat.BW1,),
    },
    job_builder=_build_s001_job,
    rotate_90=True,  # head is the short axis; print content along the label length
)
