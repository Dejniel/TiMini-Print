"""Phomemo ESC commands.

This reuses ESC/POS-shaped raster commands where the source apps do, but it is
modeled as a Phomemo family rather than a generic ESC/POS implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ....raster import PixelFormat, RasterBuffer
from ...types import PaperMode
from ..base import PrintJobRequest
from ..bitmap import build_gs_v0_blocks
from .compact import PhomemoCompactRecipe

_INIT = b"\x1b\x40"
_JUSTIFY = b"\x1b\x61"
_DENSITY_PREFIX = b"\x1f\x11\x02"
_PRINTMASTER_PRINT_MULTI_PREFIX = b"\x1f\x11\x21"
_PRINT_AND_FEED_LINES = b"\x1b\x64"
_FEED_DOTS = b"\x1b\x4a"
_MAX_RASTER_LINES_PER_BLOCK = 0xFF
_M110_MAX_RASTER_LINES_PER_BLOCK = 0xFFFF
_MANUAL_FEED_DOTS = 80
_PHOMEMO_LABEL_MODES = (PaperMode.TAG, PaperMode.PLAIN, PaperMode.BLACK_TAG)
_PRINTMASTER_M110_ROW_WIDTH = 384


@dataclass(frozen=True)
class PhomemoEscRecipe:
    paper_modes: ClassVar[tuple[PaperMode, ...]] = (PaperMode.PLAIN,)
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
        if request.ends_media_page and request.post_print_feed_count > 0:
            payload += _print_and_feed_lines(request.post_print_feed_count)
        return bytes(payload)


@dataclass(frozen=True)
class PrintMasterM110Recipe:
    # TODO: The M110/M120 recipe does not set medium type inline.
    paper_modes: ClassVar[tuple[PaperMode, ...]] = (PaperMode.TAG,)
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


RECIPES = {
    "m02": PhomemoCompactRecipe(384, left_padding=4),
    "m02s": PhomemoCompactRecipe(576, left_padding=4, label_right_padding=12),
    "m02x": PhomemoEscRecipe(protocol_variant="m02x"),
    "m02_pro": PhomemoCompactRecipe(576, left_padding=4, label_right_padding=7),
    "t02": PhomemoCompactRecipe(384, left_padding=4),
    "m110": PhomemoCompactRecipe(384, paper_modes=_PHOMEMO_LABEL_MODES),
    "m220": PhomemoCompactRecipe(576, paper_modes=_PHOMEMO_LABEL_MODES),
    "printmaster_m110": PrintMasterM110Recipe(protocol_variant="printmaster_m110"),
    "printmaster_m120": PrintMasterM110Recipe(protocol_variant="printmaster_m120", include_print_multi=True),
}


def build_phomemo_esc_job(request: PrintJobRequest) -> bytes:
    return _recipe_for_variant(request.protocol_variant).build_job(request)


def supported_paper_modes(protocol_variant: str | None) -> tuple[PaperMode, ...]:
    return _recipe_for_variant(protocol_variant).paper_modes


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_DOTS + bytes([_MANUAL_FEED_DOTS])


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b""


def _recipe_for_variant(
    protocol_variant: str | None,
) -> PhomemoEscRecipe | PhomemoCompactRecipe | PrintMasterM110Recipe:
    try:
        return RECIPES[protocol_variant or "m02"]
    except KeyError:
        raise ValueError(f"Unsupported Phomemo ESC protocol variant: {protocol_variant}") from None


def _density(value: int | None, *, default: int) -> int:
    return _byte(value, default=default, minimum=0, maximum=255)


def _byte(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        value = default
    return max(minimum, min(maximum, int(value)))


def _density_command(density: int) -> bytes:
    return _DENSITY_PREFIX + bytes([density])


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
