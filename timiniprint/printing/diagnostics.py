from __future__ import annotations

from .. import reporting
from ..devices import PrinterDevice
from ..protocol import ImagePipelineConfig, ProtocolJob
from ..raster import PixelFormat, RasterBuffer, RasterSet
from ..rendering.converters import Page
from .settings import DitherMode
from .debug_dump import build_protocol_packet_summary
from .paper import resolve_paper
from .settings import PrintSettings


def report_raster_build(
    reporter: reporting.Reporter | None,
    *,
    device: PrinterDevice,
    settings: PrintSettings,
    page_index: int,
    page_count: int,
    page: Page,
    raster_set: RasterSet,
    is_text: bool,
    dither_mode: DitherMode,
    gamma_handle: bool,
    gamma_value: float | None,
) -> None:
    if reporter is None:
        return
    raster = next(iter(raster_set.rasters.values()))
    runtime_density = (
        None
        if device.runtime_settings is None
        else device.runtime_settings.select_density(
            is_text=is_text,
            blackening=settings.blackening,
        )
    )
    reporter.debug(
        short="Raster",
        detail=reporting.format_kv(
            f"Raster page {page_index}/{page_count}",
            source=f"{page.image.width}x{page.image.height}",
            **_raster_stats(raster),
            is_text=is_text,
            dither=dither_mode.value,
            gamma_handle=gamma_handle,
            gamma_value="<auto>" if gamma_value is None else gamma_value,
            speed=device.profile.select_speed(is_text=is_text),
            energy=device.profile.select_energy(is_text=is_text, blackening=settings.blackening),
            density=runtime_density
            if runtime_density is not None
            else device.profile.select_density(
                is_text=is_text,
                blackening=settings.blackening,
            ),
        ),
    )


def report_protocol_job_build(
    reporter: reporting.Reporter | None,
    *,
    device: PrinterDevice,
    settings: PrintSettings,
    job: ProtocolJob,
    pipeline: ImagePipelineConfig,
    page_count: int,
) -> None:
    if reporter is None:
        return
    packet_summary = build_protocol_packet_summary(device, job.payload)
    op_counts = ",".join(
        f"{op}:{count}" for op, count in packet_summary["op_counts"].items()
    )
    head_ops = ",".join(str(op or "raw") for op in packet_summary["head_ops"])
    tail_ops = ",".join(str(op or "raw") for op in packet_summary["tail_ops"])
    parse_errors = packet_summary["parse_errors"]
    paper = resolve_paper(device, settings)
    reporter.debug(
        short="Job",
        detail=reporting.format_kv(
            "Job build",
            profile=device.profile_key,
            family=device.protocol_family.value,
            variant=device.protocol_variant,
            runtime=(
                None
                if device.runtime_settings is None
                else device.runtime_settings.control_algorithm
            ),
            effective_encoding=pipeline.encoding.value,
            formats=[pixel_format.value for pixel_format in pipeline.formats],
            pages=page_count,
            blackening=settings.blackening,
            paper_preset=paper.key,
            paper_mode="<none>" if paper.paper_mode is None else paper.paper_mode.value,
            payload_bytes=len(job.payload),
            segments=len(job.payload_segments),
            steps=len(job.steps),
            packets=packet_summary["packet_count"],
        ),
    )
    reporter.debug(
        short="Job",
        detail=reporting.format_kv(
            "Protocol packets",
            ops=op_counts,
            head=head_ops,
            tail=tail_ops,
            parse_errors=parse_errors,
        ),
    )


def _raster_stats(raster: RasterBuffer) -> dict[str, object]:
    total = len(raster.pixels)
    if raster.pixel_format == PixelFormat.BW1:
        black = sum(1 for value in raster.pixels if value)
        white = total - black
        coverage = (black * 100.0 / total) if total else 0.0
        return {
            "format": raster.pixel_format.value,
            "size": f"{raster.width}x{raster.height}",
            "black": black,
            "white": white,
            "coverage": f"{coverage:.2f}%",
        }
    if not total:
        return {
            "format": raster.pixel_format.value,
            "size": f"{raster.width}x{raster.height}",
            "empty": True,
        }
    minimum = min(raster.pixels)
    maximum = max(raster.pixels)
    average = sum(raster.pixels) / total
    return {
        "format": raster.pixel_format.value,
        "size": f"{raster.width}x{raster.height}",
        "gray_min": minimum,
        "gray_max": maximum,
        "gray_avg": average,
    }
