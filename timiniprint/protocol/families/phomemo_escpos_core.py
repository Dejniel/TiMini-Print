from __future__ import annotations

from dataclasses import dataclass

from ...raster import PixelFormat
from .base import PrintJobRequest
from .bitmap import build_gs_v0_blocks

_INIT = b"\x1b\x40"
_JUSTIFY = b"\x1b\x61"
_DENSITY_PREFIX = b"\x1f\x11\x02"
_PRINT_AND_FEED_LINES = b"\x1b\x64"
_FEED_DOTS = b"\x1b\x4a"
_MAX_RASTER_LINES_PER_BLOCK = 0xFF
_MANUAL_FEED_DOTS = 80


@dataclass(frozen=True)
class PhomemoEscposRecipe:
    protocol_variant: str
    default_density: int = 4
    justification: int = 1
    include_density: bool = True

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(
                f"Unsupported Phomemo ESC/POS protocol variant: {request.protocol_variant}"
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


def build_phomemo_escpos_job(request: PrintJobRequest) -> bytes:
    return _recipe_for_variant(request.protocol_variant).build_job(request)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_DOTS + bytes([_MANUAL_FEED_DOTS])


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b""


def _recipe_for_variant(protocol_variant: str | None) -> PhomemoEscposRecipe:
    variant = protocol_variant or "m02"
    if variant in {"m02", "m02s", "m02x", "m02_pro", "t02"}:
        return PhomemoEscposRecipe(protocol_variant=variant)
    raise ValueError(f"Unsupported Phomemo ESC/POS protocol variant: {protocol_variant}")


def _density(value: int | None, *, default: int) -> int:
    if value is None:
        value = default
    return max(0, min(255, int(value)))


def _density_command(density: int) -> bytes:
    return _DENSITY_PREFIX + bytes([density])


def _justify_command(justification: int) -> bytes:
    return _JUSTIFY + bytes([max(0, min(2, int(justification)))])


def _print_and_feed_lines(lines: int) -> bytes:
    return _PRINT_AND_FEED_LINES + bytes([max(0, min(255, int(lines)))])
