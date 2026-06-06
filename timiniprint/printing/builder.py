from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from .. import reporting
from ..printing.runtime.base import PreparedRuntimeContext
from ..printing.runtime.factory import runtime_controller_for_device
from ..protocol.family import ProtocolFamily
from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.types import ImageEncoding
from ..rendering.converters import Page, PageLoader
from ..rendering.renderer import apply_page_transforms, image_to_raster_set
from .debug_markers import apply_debug_row_markers
from .diagnostics import report_protocol_job_build, report_raster_build
from .settings import PrintSettings

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
        reporter: reporting.Reporter | None = None,
    ) -> None:
        self.device = device
        self.settings = settings or PrintSettings()
        self.runtime_context = runtime_context
        self._reporter = reporter
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
        pipeline = self.protocol.resolve_image_pipeline(
            image_encoding_override=self.settings.image_encoding_override,
            pixel_format_override=self.settings.pixel_format_override,
            runtime_capabilities=self.runtime_context.capabilities,
        )
        required_formats = pipeline.formats[:1]
        gamma_handle, gamma_value = self._resolve_gray_preprocessing()
        payload_parts: list[bytes] = []
        steps = []
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
            if self.settings.debug_row_markers_interval is not None:
                raster_set = apply_debug_row_markers(
                    raster_set,
                    self.settings.debug_row_markers_interval,
                )
            report_raster_build(
                self._reporter,
                device=self.device,
                settings=self.settings,
                page_index=page_index,
                page_count=page_count,
                page=page,
                raster_set=raster_set,
                is_text=is_text,
                dither=self._use_dither(page),
                gamma_handle=gamma_handle,
                gamma_value=gamma_value,
            )
            page_job = self.protocol.build_job(
                raster_set,
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
            steps.extend(page_job.steps)
        job = ProtocolJob(
            runtime_controller=(
                self.runtime_context.runtime_controller
                or runtime_controller_for_device(self.device)
            ),
            payload_segments=tuple(payload_parts),
            steps=tuple(steps),
        )
        report_protocol_job_build(
            self._reporter,
            device=self.device,
            settings=self.settings,
            job=job,
            pipeline=pipeline,
            page_count=page_count,
        )
        return job

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
