from __future__ import annotations

from ...raster import PixelFormat
from ..encoding import pack_line, rle_encode_line
from ..family import ProtocolFamily
from ..packet import make_packet
from ..types import ImageEncoding, ImagePipelineConfig, PaperMode
from .base import BleTransportProfile, PrintJobRequest, ProtocolBehavior


TINYPRINT_EIGHT = "tinyprint_eight"
TINYPRINT_NEW = "tinyprint_new"
TINYPRINT_NEW_EIGHT = "tinyprint_new_eight"
TINYPRINT_PROFESSIONAL = "tinyprint_professional"
TINYPRINT_EIGHT_PAPER_MODES = (PaperMode.PLAIN, PaperMode.A4_SHEET)
_BLE_STANDARD_CHUNK_CAP = 512


def _speed(request: PrintJobRequest) -> int:
    if request.speed is None:
        raise ValueError(f"{request.protocol_family.value} requires speed defaults")
    return request.speed


def _tinyprint_blackening_cmd(level: int, family: ProtocolFamily | str) -> bytes:
    level = max(1, min(5, level))
    return make_packet(0xA4, bytes([0x30 + level]), family)


def _tinyprint_energy_cmd(energy: int, family: ProtocolFamily | str) -> bytes:
    if energy <= 0:
        return b""
    return make_packet(0xAF, energy.to_bytes(2, "little"), family)


def _tinyprint_print_mode_cmd(is_text: bool, family: ProtocolFamily | str) -> bytes:
    return make_packet(0xBE, bytes([1 if is_text else 0]), family)


def _tinyprint_speed_cmd(speed: int, family: ProtocolFamily | str) -> bytes:
    return make_packet(0xBD, bytes([speed & 0xFF]), family)


def _tinyprint_dev_state_cmd(family: ProtocolFamily | str) -> bytes:
    return make_packet(0xA3, b"\x00", family)


def _tinyprint_stop_print_cmd(family: ProtocolFamily | str) -> bytes:
    return make_packet(0xA6, b"\x05", family)


def _tinyprint_paper_le_check_black(amount: int, family: ProtocolFamily | str) -> bytes:
    cmd = 0xA0 if amount < 0 else 0xA1
    payload = abs(amount).to_bytes(2, "little", signed=False) + b"\x11"
    return make_packet(cmd, payload, family)


def _tinyprint_supported_paper_modes(protocol_variant: str | None) -> tuple[PaperMode, ...]:
    if protocol_variant in {TINYPRINT_EIGHT, TINYPRINT_NEW_EIGHT, TINYPRINT_PROFESSIONAL}:
        return TINYPRINT_EIGHT_PAPER_MODES
    return ()


def _left_padded_pixels(request: PrintJobRequest) -> tuple[list[int], int]:
    raster = request.require_raster(PixelFormat.BW1)
    pixels = list(raster.pixels)
    padding = max(0, request.left_padding_pixels)
    if padding == 0:
        return pixels, raster.width

    out: list[int] = []
    for row in range(raster.height):
        start = row * raster.width
        out.extend([0] * padding)
        out.extend(pixels[start : start + raster.width])
    return out, raster.width + padding


def _legacy_line_packets(
    pixels: list[int],
    width: int,
    speed: int,
    encoding: ImageEncoding,
    lsb_first: bool,
    family: ProtocolFamily | str,
    periodic_speed: bool = True,
) -> bytes:
    height = len(pixels) // width
    width_bytes = (width + 7) // 8
    out = bytearray()
    for row in range(height):
        line = pixels[row * width : (row + 1) * width]
        if encoding == ImageEncoding.LEGACY_RLE:
            rle = rle_encode_line(line)
            if len(rle) <= width_bytes:
                out += make_packet(0xBF, bytes(rle), family)
            else:
                out += make_packet(0xA2, pack_line(line, lsb_first), family)
        elif encoding == ImageEncoding.LEGACY_RAW:
            out += make_packet(0xA2, pack_line(line, lsb_first), family)
        else:
            raise ValueError(f"Unsupported legacy image encoding: {encoding.value}")
        if periodic_speed and (row + 1) % 200 == 0:
            out += _tinyprint_speed_cmd(speed, family)
    return bytes(out)


def _tinyprint_eight_tail_feed(request: PrintJobRequest) -> int:
    if request.paper_mode == PaperMode.A4_SHEET:
        if request.a4xii:
            return 500
        max_height = request.a4_sheet_max_height
        if max_height is None or max_height <= 0:
            max_height = 3800 if request.dev_dpi == 300 else 2400
        return max(0, max_height - request.require_raster(PixelFormat.BW1).height)
    if request.a4xii or not request.lsb_first:
        return 100
    dots_per_paper = 72 if request.dev_dpi == 300 else 48
    return max(0, request.post_print_feed_count + 1) * dots_per_paper


def _build_tinyprint_eight_job(request: PrintJobRequest) -> bytes:
    pixels, width = _left_padded_pixels(request)
    speed = _speed(request)
    payload = bytearray()
    payload += _tinyprint_blackening_cmd(request.blackening, request.protocol_family)
    payload += _tinyprint_energy_cmd(request.energy, request.protocol_family)
    payload += _tinyprint_print_mode_cmd(request.is_text, request.protocol_family)
    payload += _tinyprint_speed_cmd(speed, request.protocol_family)
    payload += _legacy_line_packets(
        pixels=pixels,
        width=width,
        speed=speed,
        encoding=request.image_pipeline.encoding,
        lsb_first=request.lsb_first,
        family=request.protocol_family,
    )
    payload += _tinyprint_paper_le_check_black(
        _tinyprint_eight_tail_feed(request),
        request.protocol_family,
    )
    payload += _tinyprint_dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _build_tinyprint_professional_job(request: PrintJobRequest) -> bytes:
    # TODO: Add the source-compatible LZO/0xCE payload path. This variant is a
    # Professional Printer raw/RLE fallback that keeps the separate command flow.
    pixels, width = _left_padded_pixels(request)
    speed = _speed(request)
    payload = bytearray()
    payload += _tinyprint_stop_print_cmd(request.protocol_family)
    payload += _tinyprint_blackening_cmd(request.blackening, request.protocol_family)
    payload += _tinyprint_energy_cmd(request.energy, request.protocol_family)
    payload += _tinyprint_print_mode_cmd(request.is_text, request.protocol_family)
    payload += _tinyprint_speed_cmd(speed, request.protocol_family)
    payload += _legacy_line_packets(
        pixels=pixels,
        width=width,
        speed=speed,
        encoding=request.image_pipeline.encoding,
        lsb_first=request.lsb_first,
        family=request.protocol_family,
        periodic_speed=False,
    )
    payload += _tinyprint_paper_le_check_black(
        _tinyprint_eight_tail_feed(request),
        request.protocol_family,
    )
    payload += _tinyprint_dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _tinyprint_new_energy_byte(energy: int) -> int:
    if energy <= 0:
        return 0
    return energy.to_bytes(max(1, (energy.bit_length() + 7) // 8), "big")[0]


def _tinyprint_new_mode_cmd(is_text: bool, family: ProtocolFamily | str) -> bytes:
    return make_packet(
        0xBE,
        bytes([1 if is_text else 0]),
        family,
    )


def _tinyprint_new_dev_state_cmd(family: ProtocolFamily | str) -> bytes:
    return make_packet(0xA3, b"\x00", family)


def _esc_star_24dot_payload(request: PrintJobRequest) -> bytes:
    raster = request.require_raster(PixelFormat.BW1)
    width = raster.width
    height = raster.height
    band_count = (height + 23) // 24
    pixels = list(raster.pixels)
    out = bytearray()

    for band in range(band_count):
        out += bytes([0x1B, 0x2A, 0x21, width & 0xFF, (width >> 8) & 0xFF])
        for x in range(width):
            for stripe in range(3):
                value = 0
                for bit in range(8):
                    y = (band * 24) + (stripe * 8) + bit
                    if y < height and pixels[(y * width) + x]:
                        value |= 1 << (7 - bit)
                out.append(value)
        out += bytes([0x1B, 0x4A, 0x00, 0x0A])
    return bytes(out)


def _build_tinyprint_new_job(request: PrintJobRequest, *, eight: bool) -> bytes:
    final_feed = None
    if eight and request.paper_mode == PaperMode.A4_SHEET:
        max_height = request.a4_sheet_max_height
        if max_height is None or max_height <= 0:
            max_height = 3800 if request.dev_dpi == 300 else 2400
        height = request.require_raster(PixelFormat.BW1).height
        final_feed = max(0, max_height - height) // 24
    elif eight:
        if request.one_length > 0:
            final_feed = request.one_length
        elif request.feed_padding > 0:
            final_feed = request.feed_padding

    if final_feed is None:
        final_feed = 4 if request.dev_dpi == 300 else 3

    payload = bytearray()
    payload += b"\x1B\x40\x12\x23"
    payload.append(_tinyprint_new_energy_byte(request.energy))
    payload += _tinyprint_new_mode_cmd(request.is_text, request.protocol_family)
    payload += _esc_star_24dot_payload(request)
    payload += b"\x1B\x64" + bytes([final_feed & 0xFF])
    payload += _tinyprint_new_dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _build_tinyprint_variant_job(request: PrintJobRequest) -> bytes | None:
    if request.protocol_variant == TINYPRINT_EIGHT:
        return _build_tinyprint_eight_job(request)
    if request.protocol_variant == TINYPRINT_NEW:
        return _build_tinyprint_new_job(request, eight=False)
    if request.protocol_variant == TINYPRINT_NEW_EIGHT:
        return _build_tinyprint_new_job(request, eight=True)
    if request.protocol_variant == TINYPRINT_PROFESSIONAL:
        return _build_tinyprint_professional_job(request)
    return None


BEHAVIOR = ProtocolBehavior(
    requires_speed=True,
    transport=BleTransportProfile(
        standard_chunk_cap=_BLE_STANDARD_CHUNK_CAP,
    ),
    supported_protocol_variants=(
        TINYPRINT_EIGHT,
        TINYPRINT_NEW,
        TINYPRINT_NEW_EIGHT,
        TINYPRINT_PROFESSIONAL,
    ),
    supported_paper_modes_resolver=_tinyprint_supported_paper_modes,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.LEGACY_RAW,
    ),
    image_encoding_support={
        ImageEncoding.LEGACY_RAW: (PixelFormat.BW1,),
        ImageEncoding.LEGACY_RLE: (PixelFormat.BW1,),
    },
    job_builder=_build_tinyprint_variant_job,
)
