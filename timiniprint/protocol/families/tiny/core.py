from __future__ import annotations

from ....raster import PixelFormat
from ..._prefixed_commands import (
    blackening_cmd,
    dev_state_cmd,
    energy_cmd,
    feed_paper_cmd,
    print_mode_cmd,
)
from ...encoding import build_line_packets
from ...family import ProtocolFamily
from ...packet import make_packet
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from ..bitmap import build_esc_star_raster, pad_raster


VARIANT_LINE_EIGHT = "line_eight"
VARIANT_ESC_STAR = "esc_star"
VARIANT_ESC_STAR_EIGHT = "esc_star_eight"
VARIANT_PROFESSIONAL = "professional"
EIGHT_PAPER_MODES = (PaperMode.PLAIN, PaperMode.A4_SHEET)
TINY_NOTIFY_PAUSE = bytes.fromhex("5178AE0101001070FF")
TINY_NOTIFY_RESUME = bytes.fromhex("5178AE0101000000FF")


def _speed(request: PrintJobRequest) -> int:
    if request.speed is None:
        raise ValueError(f"{request.protocol_family.value} requires speed defaults")
    return request.speed


def _stop_print_cmd(family: ProtocolFamily | str) -> bytes:
    return make_packet(0xA6, b"\x05", family)


def _paper_feed_check_black_cmd(amount: int, family: ProtocolFamily | str) -> bytes:
    cmd = 0xA0 if amount < 0 else 0xA1
    payload = abs(amount).to_bytes(2, "little", signed=False) + b"\x11"
    return make_packet(cmd, payload, family)


def _supported_paper_modes(protocol_variant: str | None) -> tuple[PaperMode, ...]:
    if protocol_variant in {VARIANT_LINE_EIGHT, VARIANT_ESC_STAR_EIGHT, VARIANT_PROFESSIONAL}:
        return EIGHT_PAPER_MODES
    return ()


def _left_padded_pixels(request: PrintJobRequest) -> tuple[list[int], int]:
    raster = pad_raster(
        request.require_raster(PixelFormat.BW1), left=max(0, request.left_padding_pixels),
    )
    return list(raster.pixels), raster.width


def _line_eight_tail_feed(request: PrintJobRequest) -> int:
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


def _build_line_eight_job(request: PrintJobRequest) -> bytes:
    pixels, width = _left_padded_pixels(request)
    speed = _speed(request)
    payload = bytearray()
    payload += blackening_cmd(request.blackening, request.protocol_family)
    payload += energy_cmd(request.energy, request.protocol_family)
    payload += print_mode_cmd(request.is_text, request.protocol_family)
    payload += feed_paper_cmd(speed, request.protocol_family)
    payload += build_line_packets(
        pixels=pixels,
        width=width,
        speed=speed,
        image_encoding=request.image_pipeline.encoding,
        lsb_first=request.lsb_first,
        protocol_family=request.protocol_family,
        line_feed_every=200,
    )
    if request.ends_media_page:
        payload += _paper_feed_check_black_cmd(
            _line_eight_tail_feed(request),
            request.protocol_family,
        )
    payload += dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _build_professional_raw_fallback_job(request: PrintJobRequest) -> bytes:
    # TODO: Add the source-compatible LZO/0xCE payload path. This variant is a
    # Professional Printer raw/RLE fallback that keeps the separate command flow.
    pixels, width = _left_padded_pixels(request)
    speed = _speed(request)
    payload = bytearray()
    payload += _stop_print_cmd(request.protocol_family)
    payload += blackening_cmd(request.blackening, request.protocol_family)
    payload += energy_cmd(request.energy, request.protocol_family)
    payload += print_mode_cmd(request.is_text, request.protocol_family)
    payload += feed_paper_cmd(speed, request.protocol_family)
    payload += build_line_packets(
        pixels=pixels,
        width=width,
        speed=speed,
        image_encoding=request.image_pipeline.encoding,
        lsb_first=request.lsb_first,
        protocol_family=request.protocol_family,
        line_feed_every=0,
    )
    if request.ends_media_page:
        payload += _paper_feed_check_black_cmd(
            _line_eight_tail_feed(request),
            request.protocol_family,
        )
    payload += dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _esc_star_energy_byte(energy: int) -> int:
    if energy <= 0:
        return 0
    return energy.to_bytes(max(1, (energy.bit_length() + 7) // 8), "big")[0]


def _esc_star_24dot_payload(request: PrintJobRequest) -> bytes:
    return build_esc_star_raster(
        request.require_raster(PixelFormat.BW1),
        band_trailer=b"\x1b\x4a\x00\x0a",
    )


def _build_esc_star_job(request: PrintJobRequest, *, eight: bool) -> bytes:
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
    payload.append(_esc_star_energy_byte(request.energy))
    payload += print_mode_cmd(request.is_text, request.protocol_family)
    payload += _esc_star_24dot_payload(request)
    if request.ends_media_page:
        payload += b"\x1B\x64" + bytes([final_feed & 0xFF])
    payload += dev_state_cmd(request.protocol_family)
    return bytes(payload)


def _build_variant_payload(request: PrintJobRequest) -> bytes | None:
    if request.protocol_variant == VARIANT_LINE_EIGHT:
        return _build_line_eight_job(request)
    if request.protocol_variant == VARIANT_ESC_STAR:
        return _build_esc_star_job(request, eight=False)
    if request.protocol_variant == VARIANT_ESC_STAR_EIGHT:
        return _build_esc_star_job(request, eight=True)
    if request.protocol_variant == VARIANT_PROFESSIONAL:
        return _build_professional_raw_fallback_job(request)
    return None


def _build_variant_job(request: PrintJobRequest) -> ProtocolPlan | None:
    payload = _build_variant_payload(request)
    if payload is None:
        return None
    return ProtocolPlan.stream(payload)


def print_controls(variant, encoding):
    if variant in (VARIANT_ESC_STAR, VARIANT_ESC_STAR_EIGHT):
        return ("energy", "text_mode")
    return ("blackening", "text_mode")


BEHAVIOR = ProtocolBehavior(
    print_controls_resolver=print_controls,
    requires_speed=True,
    supported_protocol_variants=(
        VARIANT_LINE_EIGHT,
        VARIANT_ESC_STAR,
        VARIANT_ESC_STAR_EIGHT,
        VARIANT_PROFESSIONAL,
    ),
    supported_paper_modes_resolver=_supported_paper_modes,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.TINY_RAW,
    ),
    image_encoding_support={
        ImageEncoding.TINY_RAW: (PixelFormat.BW1,),
        ImageEncoding.TINY_RLE: (PixelFormat.BW1,),
    },
    job_builder=_build_variant_job,
)
