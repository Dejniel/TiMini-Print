from __future__ import annotations

from ..compression import compress_lzo1x_1
from ..encoding import pack_line
from ..packet import make_packet
from ..plan import ProtocolPlan
from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig
from .base import BleTransportProfile, FlowControlProfile, PrintJobRequest, ProtocolBehavior


def _hex_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


V5C_CONNECT_INIT_PACKET = _hex_bytes("5688AA0001000000FF")
V5C_QUERY_STATUS_PACKET = _hex_bytes("5688A10001000000FF")
V5C_NOTIFY_PAUSE = _hex_bytes("5688A70101000107FF")
V5C_NOTIFY_RESUME = _hex_bytes("5688A70101000000FF")
# Compressed V5C jobs are emitted in 20-row bands.
_V5C_BAND_ROWS = 20

_FLOW_CONTROL = FlowControlProfile(
    pause_packets=frozenset({V5C_NOTIFY_PAUSE}),
    resume_packets=frozenset({V5C_NOTIFY_RESUME}),
)


TRANSPORT = BleTransportProfile(
    connect_packets=(V5C_CONNECT_INIT_PACKET,),
    # V5C devices need a longer settle window after the connect init packet.
    connect_delay_ms=600,
    prefer_generic_notify=True,
    flow_control=_FLOW_CONTROL,
    wait_for_flow_on_standard_write=True,
)


def _settings_payload(blackening: int, is_text: bool) -> bytes:
    level = max(1, min(5, blackening))
    # The protocol exposes only three density states even though the user-level
    # blackening setting has five steps.
    if level <= 2:
        density = 0x01
    elif level >= 4:
        density = 0x03
    else:
        density = 0x02
    mode = 0x01 if is_text else 0x02
    return bytes([density, mode])


def _a5_payload(raw_block: bytes) -> bytes:
    compressed = compress_lzo1x_1(raw_block)
    return (
        len(raw_block).to_bytes(2, "little")
        + len(compressed).to_bytes(2, "little")
        + compressed
    )


def _build_a4_frames(request: PrintJobRequest) -> bytes:
    raster = request.require_raster(PixelFormat.BW1)
    job = bytearray()
    height = raster.height
    for row in range(height):
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        job += make_packet(0xA4, pack_line(line, lsb_first=True), request.protocol_family)
    return bytes(job)


def _build_a5_frames(request: PrintJobRequest) -> bytes:
    gray_raster = request.default_raster
    if gray_raster.pixel_format not in (PixelFormat.GRAY4, PixelFormat.GRAY8):
        raise ValueError("V5C compressed jobs require GRAY4 or GRAY8 source raster")

    height = gray_raster.height
    job = bytearray()
    for row in range(0, height, _V5C_BAND_ROWS):
        rows = min(_V5C_BAND_ROWS, height - row)
        block = gray_raster.slice_rows(row, rows).packed_bytes()
        payload = _a5_payload(block)
        job += make_packet(0xA5, payload, request.protocol_family)
    return bytes(job)


def _build_payload(request: PrintJobRequest) -> bytes:
    if request.width % 8 != 0:
        raise ValueError("Width must be divisible by 8")

    job = bytearray()
    job += make_packet(
        0xA2,
        _settings_payload(request.blackening, request.is_text),
        request.protocol_family,
    )
    job += make_packet(0xA3, bytes([0x01]), request.protocol_family)
    if request.image_pipeline.encoding == ImageEncoding.V5C_A5:
        job += _build_a5_frames(request)
    else:
        job += _build_a4_frames(request)

    job += make_packet(0xA6, bytes([0x30, 0x00]), request.protocol_family)
    job += V5C_QUERY_STATUS_PACKET
    return bytes(job)


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.stream(_build_payload(request))


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1, PixelFormat.GRAY4, PixelFormat.GRAY8),
        encoding=ImageEncoding.V5C_A4,
    ),
    image_encoding_support={
        ImageEncoding.V5C_A4: (PixelFormat.BW1,),
        ImageEncoding.V5C_A5: (PixelFormat.GRAY4, PixelFormat.GRAY8),
    },
    job_builder=build_job,
)
