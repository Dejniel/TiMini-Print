from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..protocol import ImageEncoding, PaperMode
from ..raster import PixelFormat


class DitherMode(str, Enum):
    NONE = "none"
    FLOYD_STEINBERG = "floyd_steinberg"
    BAYER_4 = "bayer_4"
    BAYER_8 = "bayer_8"
    ATKINSON = "atkinson"


DEFAULT_BLACKENING = 3
DEFAULT_FEED_PADDING = 12


@dataclass
class PrintSettings:
    dither_mode: DitherMode = DitherMode.FLOYD_STEINBERG
    lsb_first: Optional[bool] = None
    text_mode: Optional[bool] = None
    rotate_90_clockwise: bool = False
    text_font: Optional[str] = None
    text_columns: Optional[int] = None
    text_wrap: bool = True
    blackening: int = DEFAULT_BLACKENING
    feed_padding: int = DEFAULT_FEED_PADDING
    trim_side_margins: bool = True
    trim_top_bottom_margins: bool = True
    pdf_pages: Optional[str] = None
    pdf_page_gap_mm: int = 5
    paper_mode: Optional[PaperMode] = None
    image_encoding_override: Optional[ImageEncoding] = None
    pixel_format_override: Optional[PixelFormat] = None
    debug_row_markers_interval: Optional[int] = None
    v5x_gamma_handle: bool = False
    v5x_gamma_value: Optional[float] = None
    v5c_gamma_handle: bool = True
    v5c_gamma_value: Optional[float] = None
