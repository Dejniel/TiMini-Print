from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator, Optional, TYPE_CHECKING

from .. import reporting
from ..printing.runtime.base import PreparedRuntimeContext
from ..protocol.family import ProtocolFamily
from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.types import ImageEncoding, ImagePipelineConfig
from ..rendering.converters import Page, PageLoader, PdfRenderer
from ..rendering.renderer import PrintImageRenderer
from .debug_markers import apply_debug_row_markers
from .diagnostics import report_protocol_job_build, report_raster_build
from .raster_job import build_raster_page_job, combine_raster_page_jobs
from .settings import DitherMode, PrintSettings

if TYPE_CHECKING:
    from ..devices import PrinterDevice


@dataclass(frozen=True)
class PreparedPageJob:
    page_index: int
    page_count: int
    job: ProtocolJob


class PrintJobBuilder:
    """Build printable jobs from files for a resolved ``PrinterDevice``."""

    def __init__(
        self,
        device: PrinterDevice,
        settings: Optional[PrintSettings] = None,
        page_loader: Optional[PageLoader] = None,
        pdf_renderer: PdfRenderer | None = None,
        image_renderer: PrintImageRenderer | None = None,
        runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
        reporter: reporting.Reporter | None = None,
    ) -> None:
        self.device = device
        self.settings = settings or PrintSettings()
        self.runtime_context = runtime_context
        self._reporter = reporter
        self.image_renderer = image_renderer or PrintImageRenderer()
        pdf_page_gap_px = self._mm_to_px(self.settings.pdf_page_gap_mm, self.device.profile.dev_dpi)
        self.page_loader = page_loader or PageLoader(
            text_font=self.settings.text_font,
            text_columns=self.settings.text_columns,
            text_wrap=self.settings.text_wrap,
            trim_side_margins=self.settings.trim_side_margins,
            trim_top_bottom_margins=self.settings.trim_top_bottom_margins,
            pdf_pages=self.settings.pdf_pages,
            pdf_page_gap_px=pdf_page_gap_px,
            pdf_renderer=pdf_renderer,
        )
        self.protocol = PrinterProtocol(device)

    def build_from_file(self, path: str) -> ProtocolJob:
        """Load a file, rasterize it, and build one printable protocol job."""
        pipeline = self._resolve_image_pipeline()
        page_count = 0
        page_jobs: list[ProtocolJob] = []
        for prepared in self._iter_page_jobs(path, pipeline):
            page_count = prepared.page_count
            page_jobs.append(prepared.job)
        job = combine_raster_page_jobs(
            self.device,
            page_jobs,
            runtime_context=self.runtime_context,
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

    def iter_page_jobs(self, path: str) -> Iterator[PreparedPageJob]:
        """Load, rasterize, and yield printable protocol jobs one page at a time."""
        yield from self._iter_page_jobs(path, self._resolve_image_pipeline())

    def iter_from_file(self, path: str) -> Iterator[ProtocolJob]:
        """Load, rasterize, and yield page jobs without pagination metadata."""
        for prepared in self.iter_page_jobs(path):
            yield prepared.job

    def _iter_page_jobs(self, path: str, pipeline: ImagePipelineConfig) -> Iterator[PreparedPageJob]:
        self._validate_input_path(path)
        width = self._normalized_width(self.device.profile.width)
        required_formats = pipeline.formats[:1]
        gamma_handle, gamma_value = self._resolve_gray_preprocessing(pipeline)
        with self.page_loader.open(path, width) as pages:
            page_count = pages.page_count
            for page_index, page in enumerate(pages, start=1):
                page = self.image_renderer.transform_page(
                    page,
                    rotate_90_clockwise=self.settings.rotate_90_clockwise,
                )
                is_text = self._select_text_mode(page)
                raster_set = self.image_renderer.raster_set(
                    page.image,
                    required_formats,
                    dither_mode=self._dither_mode(page),
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
                    dither_mode=self._dither_mode(page),
                    gamma_handle=gamma_handle,
                    gamma_value=gamma_value,
                )
                page_job = build_raster_page_job(
                    self.device,
                    raster_set,
                    is_text=is_text,
                    settings=self.settings,
                    runtime_context=self.runtime_context,
                    page_index=page_index,
                    page_count=page_count,
                )
                yield PreparedPageJob(page_index=page_index, page_count=page_count, job=page_job)

    def _resolve_image_pipeline(self) -> ImagePipelineConfig:
        return self.protocol.resolve_image_pipeline(
            image_encoding_override=self.settings.image_encoding_override,
            pixel_format_override=self.settings.pixel_format_override,
            runtime_capabilities=self.runtime_context.capabilities,
        )

    def _resolve_gray_preprocessing(self, pipeline: ImagePipelineConfig) -> tuple[bool, Optional[float]]:
        if self.device.protocol_family == ProtocolFamily.V5C and pipeline.encoding == ImageEncoding.V5C_A5:
            return self.settings.v5c_gamma_handle, self.settings.v5c_gamma_value
        if self.device.protocol_family == ProtocolFamily.V5X and pipeline.encoding == ImageEncoding.V5X_GRAY:
            return self.settings.v5x_gamma_handle, self.settings.v5x_gamma_value
        return False, None

    def _dither_mode(self, page: Page) -> DitherMode:
        return self.settings.dither_mode if page.dither else DitherMode.NONE

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
