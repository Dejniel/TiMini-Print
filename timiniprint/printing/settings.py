from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..protocol import ImageEncoding
from ..protocol.family import ProtocolFamily
from ..raster import DitherMode, PixelFormat


DEFAULT_BLACKENING = 3
DEFAULT_FEED_PADDING = 12


@dataclass
class PrintSettings:
    """File/raster print options; creating settings performs no I/O.

    ``paper_preset_key=None`` selects the resolved profile's default paper.
    For file printing, ``text_mode=None`` derives text/image treatment from the
    source; a bool overrides it. ``blackening`` is a user level 1..5, not wire density.
    ``feed_padding`` retains recipe-specific units; it is not a millimetre gap.
    ``page_gap_mm`` controls spacing between PDF pages where the recipe uses it.

    A lone ``pixel_format_override`` lets PrinterProtocol choose a compatible
    codec; ``image_encoding_override`` requests a specific codec. Incompatible
    format/codec choices are rejected during building, while family runtime
    fallbacks still apply. ``lsb_first=None`` keeps profile bit ordering.
    Query PrinterProtocol's available controls/formats for the selected paper:
    having a settings field does not mean every recipe implements that option.
    """

    dither_mode: DitherMode = DitherMode.ATKINSON
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
    page_gap_mm: int = 5
    paper_preset_key: Optional[str] = None
    image_encoding_override: Optional[ImageEncoding] = None
    pixel_format_override: Optional[PixelFormat] = None
    debug_row_markers_interval: Optional[int] = None
    v5x_gamma_handle: bool = False
    v5x_gamma_value: Optional[float] = None
    v5c_gamma_handle: bool = True
    v5c_gamma_value: Optional[float] = None


def resolve_gray_preprocessing(
    settings: PrintSettings,
    protocol_family: ProtocolFamily,
    encoding: ImageEncoding,
) -> tuple[bool, Optional[float]]:
    if protocol_family == ProtocolFamily.V5C and encoding == ImageEncoding.V5C_A5:
        return settings.v5c_gamma_handle, settings.v5c_gamma_value
    if protocol_family == ProtocolFamily.V5X and encoding == ImageEncoding.V5X_GRAY:
        return settings.v5x_gamma_handle, settings.v5x_gamma_value
    return False, None
