"""Phomemo ESC commands.

This reuses ESC/POS-shaped raster commands where the source apps do, but it is
modeled as a Phomemo family rather than a generic ESC/POS implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....raster import PixelFormat, RasterBuffer
from ...types import PaperMode
from ..base import PrintJobRequest
from ..bitmap import build_gs_v0_blocks

_INIT = b"\x1b\x40"
_JUSTIFY = b"\x1b\x61"
_DENSITY_PREFIX = b"\x1f\x11\x02"
_M110_SPEED_PREFIX = b"\x1b\x4e\x0d"
_M110_DENSITY_PREFIX = b"\x1b\x4e\x04"
_M110_MEDIA_PREFIX = b"\x1f\x11"
_M110_FOOTER = b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00"
_PRINTMASTER_PRINT_MULTI_PREFIX = b"\x1f\x11\x21"
_PRINT_AND_FEED_LINES = b"\x1b\x64"
_FEED_DOTS = b"\x1b\x4a"
_MAX_RASTER_LINES_PER_BLOCK = 0xFF
_M110_MAX_RASTER_LINES_PER_BLOCK = 0xFFFF
_MANUAL_FEED_DOTS = 80
_M02_VARIANTS = frozenset({"m02", "m02s", "m02x", "m02_pro", "t02"})
_M110_VARIANTS = frozenset({"m110", "m220"})
_PRINTMASTER_M110_VARIANTS = frozenset({"printmaster_m110", "printmaster_m120"})
_PRINTMASTER_M110_ROW_WIDTH = 384
_M110_PAPER_MEDIA = {
    PaperMode.TAG: 0x0A,
    PaperMode.PLAIN: 0x0B,
    PaperMode.BLACK_TAG: 0x26,
}


@dataclass(frozen=True)
class PhomemoEscRecipe:
    protocol_variant: str
    default_density: int = 4
    justification: int = 1
    include_density: bool = True

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(
                f"Unsupported Phomemo ESC protocol variant: {request.protocol_variant}"
            )
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()

        payload = bytearray()
        payload += _INIT
        payload += _justify_command(self.justification)
        if self.include_density:
            payload += _density_command(_density(request.density, default=self.default_density))
        payload += build_gs_v0_blocks(
            raster,
            max_lines_per_block=_MAX_RASTER_LINES_PER_BLOCK,
            lsb_first=False,
            mode=0,
        )
        if request.post_print_feed_count > 0:
            payload += _print_and_feed_lines(request.post_print_feed_count)
        return bytes(payload)


@dataclass(frozen=True)
class PhomemoM110Recipe:
    protocol_variant: str
    default_speed: int = 5
    default_density: int = 10
    default_paper_mode: PaperMode = PaperMode.TAG

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(
                f"Unsupported Phomemo ESC protocol variant: {request.protocol_variant}"
            )
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()

        payload = bytearray()
        payload += _m110_speed_command(
            _byte(request.speed, default=self.default_speed, minimum=1, maximum=5)
        )
        payload += _m110_density_command(
            _byte(request.density, default=self.default_density, minimum=1, maximum=15)
        )
        payload += _m110_media_command(request.paper_mode or self.default_paper_mode)
        payload += build_gs_v0_blocks(
            raster,
            max_lines_per_block=_M110_MAX_RASTER_LINES_PER_BLOCK,
            lsb_first=False,
            mode=0,
        )
        payload += _M110_FOOTER
        return bytes(payload)


@dataclass(frozen=True)
class PrintMasterM110Recipe:
    protocol_variant: str
    include_print_multi: bool = False

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(
                f"Unsupported Phomemo ESC protocol variant: {request.protocol_variant}"
        )
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        raster = _require_printmaster_m110_width(raster)

        payload = bytearray()
        payload += _INIT
        if self.include_print_multi:
            payload += _printmaster_print_multi_command(1)
        payload += build_gs_v0_blocks(
            raster,
            max_lines_per_block=_M110_MAX_RASTER_LINES_PER_BLOCK,
            lsb_first=False,
            mode=0,
        )
        return bytes(payload)


def build_phomemo_esc_job(request: PrintJobRequest) -> bytes:
    return _recipe_for_variant(request.protocol_variant).build_job(request)


def supported_paper_modes(protocol_variant: str | None) -> tuple[PaperMode, ...]:
    variant = protocol_variant or "m02"
    if variant in _M110_VARIANTS:
        return tuple(_M110_PAPER_MEDIA)
    if variant in _PRINTMASTER_M110_VARIANTS:
        # TODO: Print Master has paper/material commands, but the recovered
        # normal M110/M120 print methods do not set medium type inline.
        return (PaperMode.TAG,)
    return (PaperMode.PLAIN,)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_DOTS + bytes([_MANUAL_FEED_DOTS])


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b""


def _recipe_for_variant(
    protocol_variant: str | None,
) -> PhomemoEscRecipe | PhomemoM110Recipe | PrintMasterM110Recipe:
    variant = protocol_variant or "m02"
    if variant in _M02_VARIANTS:
        return PhomemoEscRecipe(protocol_variant=variant)
    if variant in _M110_VARIANTS:
        return PhomemoM110Recipe(protocol_variant=variant)
    if variant == "printmaster_m110":
        return PrintMasterM110Recipe(protocol_variant=variant)
    if variant == "printmaster_m120":
        return PrintMasterM110Recipe(protocol_variant=variant, include_print_multi=True)
    raise ValueError(f"Unsupported Phomemo ESC protocol variant: {protocol_variant}")


def _density(value: int | None, *, default: int) -> int:
    return _byte(value, default=default, minimum=0, maximum=255)


def _byte(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        value = default
    return max(minimum, min(maximum, int(value)))


def _density_command(density: int) -> bytes:
    return _DENSITY_PREFIX + bytes([density])


def _m110_speed_command(speed: int) -> bytes:
    return _M110_SPEED_PREFIX + bytes([speed])


def _m110_density_command(density: int) -> bytes:
    return _M110_DENSITY_PREFIX + bytes([density])


def _m110_media_command(paper_mode: PaperMode) -> bytes:
    media_type = _M110_PAPER_MEDIA.get(paper_mode)
    if media_type is None:
        raise ValueError(f"Unsupported Phomemo M110 paper mode: {paper_mode.value}")
    return _M110_MEDIA_PREFIX + bytes([media_type])


def _printmaster_print_multi_command(quantity: int) -> bytes:
    return _PRINTMASTER_PRINT_MULTI_PREFIX + bytes([max(1, min(255, int(quantity)))])


def _require_printmaster_m110_width(raster: RasterBuffer) -> RasterBuffer:
    if raster.pixel_format != PixelFormat.BW1:
        raise ValueError("Print Master M110/M120 jobs require a bw1 raster")
    if raster.width != _PRINTMASTER_M110_ROW_WIDTH:
        raise ValueError(
            f"Print Master M110/M120 jobs require {_PRINTMASTER_M110_ROW_WIDTH}px raster width"
        )
    return raster


def _justify_command(justification: int) -> bytes:
    return _JUSTIFY + bytes([max(0, min(2, int(justification)))])


def _print_and_feed_lines(lines: int) -> bytes:
    return _PRINT_AND_FEED_LINES + bytes([max(0, min(255, int(lines)))])
