from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..protocol.job import PrinterProtocol, ProtocolJob
from ..protocol.types import ImagePipelineConfig
from ..raster import RasterSet
from .runtime.base import PreparedRuntimeContext
from .runtime.factory import runtime_controller_for_device
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
    image_pipeline: ImagePipelineConfig | None = None,
) -> ProtocolJob:
    """Build one protocol page from an already prepared raster."""
    effective_settings = settings or PrintSettings()
    return PrinterProtocol(device).build_job(
        raster_set,
        is_text=is_text,
        blackening=effective_settings.blackening,
        feed_padding=effective_settings.feed_padding,
        paper_mode=effective_settings.paper_mode,
        lsb_first=effective_settings.lsb_first,
        image_pipeline=image_pipeline,
        image_encoding_override=effective_settings.image_encoding_override,
        pixel_format_override=effective_settings.pixel_format_override,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_context.capabilities,
        runtime_controller=runtime_context.runtime_controller,
    )


def combine_raster_page_jobs(
    device: PrinterDevice,
    page_jobs: Iterable[ProtocolJob],
    *,
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
) -> ProtocolJob:
    jobs = tuple(page_jobs)
    return ProtocolJob(
        runtime_controller=(
            runtime_context.runtime_controller
            or runtime_controller_for_device(device)
        ),
        payload_segments=tuple(job.payload for job in jobs),
        steps=tuple(step for job in jobs for step in job.steps),
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
    return combine_raster_page_jobs(
        device,
        (page_job,),
        runtime_context=runtime_context,
    )
