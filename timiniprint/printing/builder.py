from __future__ import annotations

import os
from typing import Iterable, Optional, TYPE_CHECKING

from ..printing.runtime.base import PreparedRuntimeContext
from ..protocol.family import ProtocolFamily
from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.steps import ProtocolStep, ProtocolStepOperation
from ..protocol.types import ImageEncoding
from ..raster import PixelFormat, RasterBuffer, RasterSet
from ..rendering.converters import Page, PageLoader
from ..rendering.renderer import apply_page_transforms, image_to_raster_set
from .settings import PrintSettings


_MAX_JOB_ROWS_ENV = "TIMINI_PRINT_MAX_JOB_ROWS"


def _resolve_max_job_rows() -> int:
    """Return the env-configured per-job row ceiling, or 0 if unset.

    Some printer firmwares (notably MXW01 v1.9.3.1.2 in the V5X family)
    silently truncate jobs taller than a few hundred rows of 384-px-wide
    raster. Setting ``TIMINI_PRINT_MAX_JOB_ROWS=200`` (or similar) makes
    the builder split tall pages into multiple back-to-back V5X sessions,
    each below the firmware ceiling, sent as separate ``ProtocolStep``s
    so the runtime can pace them and the printer never receives more
    rows in one session than it can render.

    Returns 0 (= no splitting) when the env var is unset, empty,
    non-numeric, or non-positive.
    """
    raw = os.environ.get(_MAX_JOB_ROWS_ENV)
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0

if TYPE_CHECKING:
    from ..devices import PrinterDevice


class PrintJobBuilder:
    """Build printable jobs from files for a resolved ``PrinterDevice``."""

    def __init__(
        self,
        device: PrinterDevice,
        settings: Optional[PrintSettings] = None,
        page_loader: Optional[PageLoader] = None,
        runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
    ) -> None:
        self.device = device
        self.settings = settings or PrintSettings()
        self.runtime_context = runtime_context
        pdf_page_gap_px = self._mm_to_px(self.settings.pdf_page_gap_mm, self.device.profile.dev_dpi)
        self.page_loader = page_loader or PageLoader(
            text_font=self.settings.text_font,
            text_columns=self.settings.text_columns,
            text_wrap=self.settings.text_wrap,
            trim_side_margins=self.settings.trim_side_margins,
            trim_top_bottom_margins=self.settings.trim_top_bottom_margins,
            pdf_pages=self.settings.pdf_pages,
            pdf_page_gap_px=pdf_page_gap_px,
        )
        self.protocol = PrinterProtocol(device)

    def build_from_file(self, path: str) -> ProtocolJob:
        """Load a file, rasterize it, and build one printable protocol job."""
        self._validate_input_path(path)
        width = self._normalized_width(self.device.profile.width)
        pages = self.page_loader.load(path, width)
        pages = apply_page_transforms(pages, rotate_90_clockwise=self.settings.rotate_90_clockwise)
        required_formats = self.protocol.resolve_image_pipeline(
            image_encoding_override=self.settings.image_encoding_override,
            pixel_format_override=self.settings.pixel_format_override,
            runtime_capabilities=self.runtime_context.capabilities,
        ).formats[:1]
        gamma_handle, gamma_value = self._resolve_gray_preprocessing()
        max_job_rows = _resolve_max_job_rows()
        payload_parts: list[bytes] = []
        steps: list[ProtocolStep] = []
        page_count = len(pages)
        for page_index, page in enumerate(pages, start=1):
            is_text = self._select_text_mode(page)
            raster_set = image_to_raster_set(
                page.image,
                required_formats,
                dither=self._use_dither(page),
                gamma_handle=gamma_handle,
                gamma_value=gamma_value,
            )
            for segment_index, segment_set in enumerate(
                self._split_raster_for_max_rows(raster_set, max_job_rows)
            ):
                page_job = self.protocol.build_job(
                    segment_set,
                    is_text=is_text,
                    blackening=self.settings.blackening,
                    feed_padding=self.settings.feed_padding,
                    paper_mode=self.settings.paper_mode,
                    lsb_first=self._lsb_first(),
                    image_encoding_override=self.settings.image_encoding_override,
                    pixel_format_override=self.settings.pixel_format_override,
                    page_index=page_index,
                    page_count=page_count,
                    runtime_capabilities=self.runtime_context.capabilities,
                    runtime_controller=self.runtime_context.runtime_controller,
                )
                payload_parts.append(page_job.payload)
                if page_job.steps:
                    steps.extend(page_job.steps)
                elif max_job_rows > 0 and segment_index >= 0:
                    # When pagination is in effect we want each sub-job to be
                    # sent as a discrete protocol step so the runtime can pace
                    # them across the same connection without bleeding the
                    # session boundary into the wrong characteristic.
                    steps.append(
                        ProtocolStep(
                            label=f"page{page_index}-seg{segment_index + 1}",
                            data=page_job.payload,
                            operation=ProtocolStepOperation.SEND,
                            include_in_payload=False,
                        )
                    )
        return ProtocolJob(
            runtime_controller=(
                self.runtime_context.runtime_controller
                or self.protocol.create_runtime_controller()
            ),
            payload_segments=tuple(payload_parts),
            steps=tuple(steps),
        )

    def _split_raster_for_max_rows(
        self, raster_set: RasterSet, max_job_rows: int
    ) -> Iterable[RasterSet]:
        """Yield raster_set or a sequence of sub-sets each at most max_job_rows tall.

        If max_job_rows is 0 (the default) or the raster fits, the original
        raster_set is yielded unchanged.
        """
        if max_job_rows <= 0:
            yield raster_set
            return
        height = raster_set.height
        if height <= max_job_rows:
            yield raster_set
            return
        for start_row in range(0, height, max_job_rows):
            row_count = min(max_job_rows, height - start_row)
            yield RasterSet(
                rasters={
                    pixel_format: raster.slice_rows(start_row, row_count)
                    for pixel_format, raster in raster_set.rasters.items()
                }
            )

    def _resolve_gray_preprocessing(self) -> tuple[bool, Optional[float]]:
        pipeline = self.protocol.resolve_image_pipeline(
            image_encoding_override=self.settings.image_encoding_override,
            pixel_format_override=self.settings.pixel_format_override,
            runtime_capabilities=self.runtime_context.capabilities,
        )
        if self.device.protocol_family == ProtocolFamily.V5C and pipeline.encoding == ImageEncoding.V5C_A5:
            return self.settings.v5c_gamma_handle, self.settings.v5c_gamma_value
        if self.device.protocol_family == ProtocolFamily.V5X and pipeline.encoding == ImageEncoding.V5X_GRAY:
            return self.settings.v5x_gamma_handle, self.settings.v5x_gamma_value
        return False, None

    def _use_dither(self, page: Page) -> bool:
        return self.settings.dither and page.dither

    def _lsb_first(self) -> bool | None:
        return self.settings.lsb_first

    def _select_text_mode(self, page: Page) -> bool:
        if self.settings.text_mode is not None:
            return self.settings.text_mode
        return page.is_text

    @staticmethod
    def _mm_to_px(mm: int, dpi: int) -> int:
        if mm <= 0:
            return 0
        return max(0, int(round(mm * dpi / 25.4)))

    @staticmethod
    def _normalized_width(width: int) -> int:
        if width % 8 == 0:
            return width
        return width - (width % 8)

    def _validate_input_path(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        supported = self.page_loader.supported_extensions
        if ext not in supported:
            raise ValueError("Supported formats: " + ", ".join(sorted(supported)))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
