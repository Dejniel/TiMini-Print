"""Uncompressed Phomemo compact jobs and their raster placement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ....raster import PixelFormat, RasterBuffer
from ...types import PaperMode
from ..base import PrintJobRequest
from ..bitmap import build_gs_v0_blocks, pad_raster

_INIT = b"\x1b\x40"
_DENSITY = b"\x1f\x11\x02"
_DENSITY_COEFFICIENT = b"\x1f\x11\x37"
_MEDIA = b"\x1f\x11"
_UNCOMPRESSED = b"\x1f\x11\x35\x00"
_FEED = b"\x1b\x64\x02"
_DENSITY_LEVELS = ((1, 100), (2, 100), (4, 100), (4, 150))
_PAPER_MEDIA = {PaperMode.PLAIN: 0x0B, PaperMode.TAG: 0x0B, PaperMode.BLACK_TAG: 0x26}


@dataclass(frozen=True)
class PhomemoCompactRecipe:
    print_controls: ClassVar[tuple[str, ...]] = ("density",)
    content_width: int
    left_padding: int = 0
    label_right_padding: int = 0
    paper_modes: tuple[PaperMode, ...] = (PaperMode.PLAIN, PaperMode.TAG)

    def build_job(self, request: PrintJobRequest) -> bytes:
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        if raster.width > self.content_width:
            raise ValueError(f"Phomemo content width must not exceed {self.content_width}px")
        paper_mode = request.paper_mode or self.paper_modes[0]
        if paper_mode not in self.paper_modes:
            raise ValueError(f"Unsupported Phomemo paper mode: {paper_mode.value}")
        raster = self._place_raster(raster, paper_mode)

        payload = bytearray()
        if request.is_first_page:
            level = 2 if request.density is None else int(request.density)
            density, coefficient = _DENSITY_LEVELS[max(1, min(4, level)) - 1]
            payload += _INIT
            payload += _DENSITY + bytes([density])
            payload += _DENSITY_COEFFICIENT + bytes([coefficient])
            payload += _MEDIA + bytes([_PAPER_MEDIA[paper_mode]])
            payload += _UNCOMPRESSED
        payload += build_gs_v0_blocks(raster, max_lines_per_block=0xFFFF)
        if request.is_last_page:
            payload += _FEED * 2
        elif request.ends_media_page:
            payload += _FEED
        return bytes(payload)

    def _place_raster(self, raster: RasterBuffer, paper_mode: PaperMode) -> RasterBuffer:
        right_padding = 0
        if paper_mode is PaperMode.TAG and self.label_right_padding:
            right_padding = self.content_width + self.label_right_padding - raster.width
        return pad_raster(raster, left=self.left_padding, right=right_padding)
