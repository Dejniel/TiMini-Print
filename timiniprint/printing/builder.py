from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator, Optional, TYPE_CHECKING

from .. import reporting
from ..printing.runtime.base import PreparedRuntimeContext
from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.types import ImagePipelineConfig
from ..rendering.converters import PdfRenderer
from ..rendering.formats import SUPPORTED_DOCUMENT_EXTENSIONS
from ..rendering.renderer import PrintImageRenderer
from .debug_markers import apply_debug_row_markers
from .diagnostics import report_protocol_job_build, report_raster_build
from .document_renderer import DocumentRenderer, RenderDocument
from .raster_job import build_raster_page_job, combine_raster_page_jobs
from .settings import PrintSettings

if TYPE_CHECKING:
    from ..devices import PrinterDevice


@dataclass(frozen=True)
class PreparedPageJob:
    page_index: int
    page_count: int
    job: ProtocolJob
    image_pipeline: ImagePipelineConfig


class PrintJobBuilder:
    """Build printable jobs from files for a resolved ``PrinterDevice``."""

    def __init__(
        self,
        device: PrinterDevice,
        settings: Optional[PrintSettings] = None,
        document_renderer: DocumentRenderer | None = None,
        pdf_renderer: PdfRenderer | None = None,
        image_renderer: PrintImageRenderer | None = None,
        runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
        reporter: reporting.Reporter | None = None,
    ) -> None:
        self.device = device
        self.settings = settings or PrintSettings()
        self.runtime_context = runtime_context
        self._reporter = reporter
        self.document_renderer = document_renderer or DocumentRenderer(
            pdf_renderer=pdf_renderer,
            image_renderer=image_renderer,
        )

    def build_from_file(self, path: str) -> ProtocolJob:
        """Load a file, rasterize it, and build one printable protocol job."""
        page_count = 0
        pipeline: ImagePipelineConfig | None = None
        page_jobs: list[ProtocolJob] = []
        for prepared in self._iter_page_jobs(path):
            page_count = prepared.page_count
            page_jobs.append(prepared.job)
            if pipeline is None:
                pipeline = prepared.image_pipeline
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
            pipeline=pipeline or self._default_image_pipeline(),
            page_count=page_count,
        )
        return job

    def iter_page_jobs(self, path: str) -> Iterator[PreparedPageJob]:
        """Load, rasterize, and yield printable protocol jobs one page at a time."""
        yield from self._iter_page_jobs(path)

    def _iter_page_jobs(self, path: str) -> Iterator[PreparedPageJob]:
        self._validate_input_path(path)
        plan = self.document_renderer.plan_document(
            RenderDocument(path),
            self.device,
            self.settings,
        )
        page_count = plan.page_count
        for page in plan.pages:
            rendered = self.document_renderer.print_page(
                plan,
                page,
                self.device,
                self.settings,
                runtime_capabilities=self.runtime_context.capabilities,
            )
            raster_set = rendered.raster_set
            if self.settings.debug_row_markers_interval is not None:
                raster_set = apply_debug_row_markers(
                    raster_set,
                    self.settings.debug_row_markers_interval,
                )
            report_raster_build(
                self._reporter,
                device=self.device,
                settings=self.settings,
                page_index=page.number,
                page_count=page_count,
                page=rendered.source_page,
                raster_set=raster_set,
                is_text=rendered.is_text,
                dither_mode=rendered.dither_mode,
                gamma_handle=rendered.gamma_handle,
                gamma_value=rendered.gamma_value,
            )
            page_job = build_raster_page_job(
                self.device,
                raster_set,
                is_text=rendered.is_text,
                settings=self.settings,
                runtime_context=self.runtime_context,
                page_index=page.number,
                page_count=page_count,
                image_pipeline=rendered.image_pipeline,
            )
            yield PreparedPageJob(
                page_index=page.number,
                page_count=page_count,
                job=page_job,
                image_pipeline=rendered.image_pipeline,
            )

    def _default_image_pipeline(self) -> ImagePipelineConfig:
        return PrinterProtocol(self.device).resolve_image_pipeline(
            image_encoding_override=self.settings.image_encoding_override,
            pixel_format_override=self.settings.pixel_format_override,
            runtime_capabilities=self.runtime_context.capabilities,
        )

    def _validate_input_path(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        supported = SUPPORTED_DOCUMENT_EXTENSIONS
        if ext not in supported:
            raise ValueError("Supported formats: " + ", ".join(sorted(supported)))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
