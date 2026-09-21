from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from ..devices.device import PrinterDevice
from ..protocol import ImageEncoding, ImagePipelineConfig, PrinterProtocol
from ..protocol.runtime import RuntimePrintCapabilities
from ..protocol.family import ProtocolFamily
from ..raster import DitherMode, PixelFormat


DEFAULT_BLACKENING = 3
DEFAULT_FEED_PADDING = 12


class ImageMode(str, Enum):
    """User-facing image conversion, independent of wire encoding/gray depth."""

    GRAYSCALE = "grayscale"
    ATKINSON = "atkinson"
    FLOYD_STEINBERG = "floyd_steinberg"
    BAYER_4 = "bayer_4"
    BAYER_8 = "bayer_8"
    THRESHOLD = "threshold"

    @property
    def dither_mode(self) -> DitherMode:
        """Monochrome conversion; grayscale fallback uses Atkinson."""
        if self is ImageMode.GRAYSCALE:
            return DitherMode.ATKINSON
        if self is ImageMode.THRESHOLD:
            return DitherMode.NONE
        return DitherMode(self.value)


@dataclass
class PrintSettings:
    """File/raster print options; creating settings performs no I/O.

    ``paper_preset_key=None`` selects the resolved profile's default paper.
    For file printing, ``text_mode=None`` derives text/image treatment from the
    source; a bool overrides it. ``blackening`` is a user level 1..5, not wire density.
    ``feed_padding`` retains recipe-specific units; it is not a millimetre gap.
    ``page_gap_mm`` controls spacing between PDF pages where the recipe uses it.

    ``image_mode=None`` prefers grayscale when supported, otherwise Atkinson.
    ``available_image_modes()`` returns selectable modes, preferred first, for
    the selected paper and session. ``image_encoding_override`` is an expert
    codec constraint, not a second image-mode selector. Runtime fallbacks still
    apply. ``lsb_first=None`` keeps profile bit ordering.
    """

    image_mode: ImageMode | None = None
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
    debug_row_markers_interval: Optional[int] = None
    v5x_gamma_handle: bool = False
    v5x_gamma_value: Optional[float] = None
    v5c_gamma_handle: bool = True
    v5c_gamma_value: Optional[float] = None

    def __post_init__(self) -> None:
        if self.image_mode is not None:
            self.image_mode = ImageMode(self.image_mode)

    def available_image_modes(
        self, device: PrinterDevice, *,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> tuple[ImageMode, ...]:
        """Selectable modes, preferred first; no I/O and no mutation of settings.

        Recompute after preparing the printer or changing paper. This ignores
        the current ``image_mode`` selection. UI clients show the first choice
        when no explicit selection exists, not an extra Automatic option.
        """
        formats = self._image_formats(device, runtime_capabilities)
        return tuple(mode for mode in ImageMode if (
            any(fmt is not PixelFormat.BW1 for fmt in formats)
            if mode is ImageMode.GRAYSCALE else PixelFormat.BW1 in formats
        ))

    def _image_formats(
        self, device: PrinterDevice, runtime_capabilities: RuntimePrintCapabilities | None,
    ) -> tuple[PixelFormat, ...]:
        protocol = PrinterProtocol(device)
        formats = protocol.supported_pixel_formats(
            paper_preset_key=self.paper_preset_key, runtime_capabilities=runtime_capabilities,
        )
        if self.image_encoding_override is None:
            return formats
        # Validate the codec once; filter only incompatible format/codec pairs.
        encoding = protocol.resolve_image_pipeline(
            paper_preset_key=self.paper_preset_key,
            image_encoding_override=self.image_encoding_override,
            runtime_capabilities=runtime_capabilities,
        ).encoding
        compatible = []
        for fmt in formats:
            try:
                pipeline = protocol.resolve_image_pipeline(paper_preset_key=self.paper_preset_key,
                    image_encoding_override=encoding, pixel_format_override=fmt,
                    runtime_capabilities=runtime_capabilities)
            except ValueError:
                continue
            if pipeline.default_format is fmt:
                compatible.append(fmt)
        return tuple(compatible)

    def resolve_image_pipeline(
        self, device: PrinterDevice, *,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
        raster_formats: tuple[PixelFormat, ...] | None = None,
    ) -> ImagePipelineConfig:
        """Resolve the user choice through the protocol's existing codec rules.

        File/preview callers omit ``raster_formats``. Prepared-raster callers
        provide the formats they actually have: this never converts their data.
        Unsupported explicit modes raise ValueError. A known negative grayscale
        capability retains the protocol's monochrome fallback (Atkinson for
        file rendering). Low-level callers can still use PrinterProtocol with
        an explicit ImagePipelineConfig or pixel format.
        """
        mode = None if self.image_mode is None else ImageMode(self.image_mode)
        selection_capabilities = runtime_capabilities
        if (runtime_capabilities is not None and runtime_capabilities.supports_gray is False
                and (mode is ImageMode.GRAYSCALE or self.image_encoding_override is not None)):
            # Select the requested gray input first, then let the existing
            # protocol fallback resolve the effective monochrome codec.
            selection_capabilities = replace(runtime_capabilities, supports_gray=None)
        formats = self._image_formats(device, selection_capabilities)
        if raster_formats is not None:
            formats = tuple(fmt for fmt in formats if fmt in raster_formats)
        if mode is None:
            mode = (ImageMode.GRAYSCALE if any(fmt is not PixelFormat.BW1 for fmt in formats)
                    else ImageMode.ATKINSON)
        fmt = next((fmt for fmt in formats if
                    (fmt is not PixelFormat.BW1) == (mode is ImageMode.GRAYSCALE)), None)
        if fmt is None:
            raise ValueError(f"Image mode {mode.value} is not supported by this paper/codec/raster")
        return PrinterProtocol(device).resolve_image_pipeline(
            paper_preset_key=self.paper_preset_key,
            image_encoding_override=self.image_encoding_override,
            pixel_format_override=fmt, runtime_capabilities=runtime_capabilities,
        )


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
