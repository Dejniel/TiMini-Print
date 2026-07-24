from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.types import ImagePipelineConfig, PageFlow
from ..raster import RasterSet
from .paper import apply_paper_layout_to_raster_set, resolve_paper
from .runtime.base import PreparedRuntimeContext
from .settings import PrintSettings

if TYPE_CHECKING:
    from ..devices import PrinterDevice


def build_raster_page_job(
    device: PrinterDevice,
    raster_set: RasterSet,
    *,
    is_text: bool,
    settings: PrintSettings | None = None,
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
    page_index: int = 1,
    page_count: int = 1,
    page_flow: PageFlow = PageFlow.PAGED,
    image_pipeline: ImagePipelineConfig | None = None,
    _paper_layout_applied: bool = False,
) -> ProtocolJob:
    """Build one protocol page from an already prepared raster."""
    effective_settings = settings or PrintSettings()
    paper = resolve_paper(device, effective_settings)
    if not _paper_layout_applied:
        raster_set = apply_paper_layout_to_raster_set(raster_set, paper)
    return PrinterProtocol(device).build_job(
        raster_set,
        is_text=is_text,
        blackening=effective_settings.blackening,
        feed_padding=effective_settings.feed_padding,
        paper_preset_key=paper.key,
        paper_mode=paper.paper_mode,
        lsb_first=effective_settings.lsb_first,
        image_pipeline=image_pipeline,
        image_encoding_override=effective_settings.image_encoding_override,
        pixel_format_override=effective_settings.pixel_format_override,
        page_index=page_index,
        page_count=page_count,
        page_flow=page_flow,
        runtime_capabilities=runtime_context.capabilities,
    )


def combine_raster_page_jobs(
    page_jobs: Iterable[ProtocolJob],
) -> ProtocolJob:
    jobs = tuple(page_jobs)
    return ProtocolJob(
        payload_segments=tuple(job.payload for job in jobs),
        steps=tuple(step for job in jobs for step in job.steps),
        wait_for_completion=any(job.wait_for_completion for job in jobs),
    )


def build_raster_job(
    device: PrinterDevice,
    raster_set: RasterSet,
    *,
    is_text: bool,
    settings: PrintSettings | None = None,
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
) -> ProtocolJob:
    """Build a complete one-page protocol job from an already prepared raster."""
    page_job = build_raster_page_job(
        device,
        raster_set,
        is_text=is_text,
        settings=settings,
        runtime_context=runtime_context,
    )
    return combine_raster_page_jobs((page_job,))
