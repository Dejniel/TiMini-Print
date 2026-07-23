"""InstaPrint/CorePrint small-printer command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...types import PaperMode
from ..base import PrintJobRequest
from ..bitmap import build_gs_v0_blocks

_INIT = b"\x1b\x40"
_DENSITY_PREFIX = b"\x1d\x49\xf0"
_FEED_AFTER_PRINT = b"\x0a\x0a\x0a\x0a"
_DEFAULT_DENSITY = 15
_MAX_RASTER_LINES_PER_BLOCK = 0xFFFF
_SUPPORTED_VARIANTS = frozenset({"ctp500"})


def build_instaprint_core_job(request: PrintJobRequest) -> bytes:
    variant = request.protocol_variant or "ctp500"
    if variant not in _SUPPORTED_VARIANTS:
        raise ValueError(
            f"Unsupported InstaPrint Core protocol variant: {request.protocol_variant!r}"
        )

    raster = request.require_raster(PixelFormat.BW1)
    raster.validate()

    payload = bytearray()
    payload += _INIT
    payload += _density_command(request.density)
    payload += build_gs_v0_blocks(
        raster,
        max_lines_per_block=_MAX_RASTER_LINES_PER_BLOCK,
        lsb_first=False,
        mode=0,
    )
    if request.ends_media_page:
        payload += _FEED_AFTER_PRINT
    return bytes(payload)


def supported_paper_modes(_protocol_variant: str | None) -> tuple[PaperMode, ...]:
    return (PaperMode.PLAIN,)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_AFTER_PRINT


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b""


def _density_command(density: int | None) -> bytes:
    if density is None:
        density = _DEFAULT_DENSITY
    return _DENSITY_PREFIX + bytes([max(0, min(255, int(density)))])
