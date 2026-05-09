from __future__ import annotations

from ..raster import PixelFormat, RasterBuffer, RasterSet
from ..printing.runtime.base import RuntimePrintCapabilities
from .commands import (
    blackening_cmd,
    dev_state_cmd,
    energy_cmd,
    feed_paper_cmd,
    paper_cmd,
    print_mode_cmd,
)
from .encoding import build_line_packets
from .families import PrintJobRequest, get_protocol_behavior
from .family import ProtocolFamily
from .types import ImagePipelineConfig, PaperMode


def _build_family_job(request: PrintJobRequest) -> bytes | None:
    behavior = get_protocol_behavior(request.protocol_family)
    if behavior.job_builder is None:
        return None
    return behavior.job_builder(request)


def _resolve_image_pipeline(
    protocol_family: ProtocolFamily | str,
    image_pipeline: ImagePipelineConfig | None,
) -> ImagePipelineConfig:
    family = ProtocolFamily.from_value(protocol_family)
    if image_pipeline is not None:
        return image_pipeline
    return get_protocol_behavior(family).default_image_pipeline


def _validate_request(request: PrintJobRequest) -> None:
    request.raster_set.validate()
    behavior = get_protocol_behavior(request.protocol_family)
    if behavior.supported_paper_modes_resolver is not None:
        supported_paper_modes = behavior.supported_paper_modes_resolver(
            request.protocol_variant
        )
    else:
        supported_paper_modes = behavior.supported_paper_modes
    if request.paper_mode is not None and request.paper_mode not in supported_paper_modes:
        raise ValueError(
            f"{request.protocol_family.value} does not support paper mode {request.paper_mode.value}"
        )
    supported_by_encoding = behavior.image_encoding_support.get(request.image_pipeline.encoding)
    if supported_by_encoding is None:
        raise ValueError(
            f"{request.protocol_family.value} does not support image encoding "
            f"{request.image_pipeline.encoding.value}"
        )
    supported_formats = {
        pixel_format
        for formats in behavior.image_encoding_support.values()
        for pixel_format in formats
    }
    for pixel_format in request.image_pipeline.formats:
        if pixel_format not in supported_formats:
            raise ValueError(
                f"{request.protocol_family.value} does not support raster format "
                f"{pixel_format.value}"
            )
    if request.image_pipeline.default_format not in supported_by_encoding:
        raise ValueError(
            f"{request.protocol_family.value} image encoding "
            f"{request.image_pipeline.encoding.value} does not support "
            f"{request.image_pipeline.default_format.value}"
        )
    request.require_raster(request.image_pipeline.default_format)


def _build_request(
    raster_set: RasterSet,
    is_text: bool,
    speed: int | None,
    energy: int,
    density: int | None,
    blackening: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None,
    feed_padding: int,
    dev_dpi: int,
    can_print_label: bool,
    post_print_feed_count: int,
    image_pipeline: ImagePipelineConfig | None,
    paper_mode: PaperMode | None = None,
    page_index: int = 1,
    page_count: int = 1,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> PrintJobRequest:
    family = ProtocolFamily.from_value(protocol_family)
    request = PrintJobRequest(
        raster_set=raster_set,
        image_pipeline=_resolve_image_pipeline(family, image_pipeline),
        is_text=is_text,
        speed=speed,
        energy=energy,
        blackening=blackening,
        lsb_first=lsb_first,
        protocol_family=family,
        protocol_variant=protocol_variant,
        feed_padding=feed_padding,
        dev_dpi=dev_dpi,
        can_print_label=can_print_label,
        density=density,
        post_print_feed_count=post_print_feed_count,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_capabilities,
    )
    _validate_request(request)
    return request


def _require_speed(request: PrintJobRequest) -> int:
    if request.speed is None:
        raise ValueError(f"{request.protocol_family.value} requires speed tuning")
    return request.speed


def _build_print_payload(
    pixels: list[int],
    width: int,
    is_text: bool,
    speed: int | None,
    energy: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None = None,
    can_print_label: bool = False,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
) -> bytes:
    raster = RasterBuffer(pixels=pixels, width=width, pixel_format=PixelFormat.BW1)
    return _build_print_payload_from_raster(
        raster=raster,
        is_text=is_text,
        speed=speed,
        energy=energy,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        can_print_label=can_print_label,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
    )


def _build_print_payload_from_raster(
    raster: RasterBuffer,
    is_text: bool,
    speed: int | None,
    energy: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None = None,
    can_print_label: bool = False,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
) -> bytes:
    return _build_print_payload_from_raster_set(
        raster_set=RasterSet.from_single(raster),
        is_text=is_text,
        speed=speed,
        energy=energy,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        can_print_label=can_print_label,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
    )


def _build_print_payload_from_raster_set(
    raster_set: RasterSet,
    is_text: bool,
    speed: int | None,
    energy: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None = None,
    can_print_label: bool = False,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
    page_index: int = 1,
    page_count: int = 1,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> bytes:
    request = _build_request(
        raster_set=raster_set,
        is_text=is_text,
        speed=speed,
        energy=energy,
        density=None,
        blackening=3,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        feed_padding=0,
        dev_dpi=203,
        can_print_label=can_print_label,
        post_print_feed_count=2,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_capabilities,
    )
    family_payload = _build_family_job(request)
    if family_payload is not None:
        return family_payload

    raster = request.require_raster(PixelFormat.BW1)
    payload = bytearray()
    payload += energy_cmd(energy, request.protocol_family)
    payload += print_mode_cmd(is_text, request.protocol_family)
    payload += feed_paper_cmd(_require_speed(request), request.protocol_family)
    payload += build_line_packets(
        list(raster.pixels),
        raster.width,
        _require_speed(request),
        request.image_pipeline.encoding,
        lsb_first,
        request.protocol_family,
        line_feed_every=200,
    )
    return bytes(payload)


def _build_job(
    pixels: list[int],
    width: int,
    is_text: bool,
    speed: int | None,
    energy: int,
    density: int | None,
    blackening: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    feed_padding: int,
    dev_dpi: int,
    can_print_label: bool = False,
    post_print_feed_count: int = 2,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
    protocol_variant: str | None = None,
    page_index: int = 1,
    page_count: int = 1,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> bytes:
    raster = RasterBuffer(pixels=pixels, width=width, pixel_format=PixelFormat.BW1)
    return _build_job_from_raster(
        raster=raster,
        is_text=is_text,
        speed=speed,
        energy=energy,
        density=density,
        blackening=blackening,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        feed_padding=feed_padding,
        dev_dpi=dev_dpi,
        can_print_label=can_print_label,
        post_print_feed_count=post_print_feed_count,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_capabilities,
    )


def _build_job_from_raster(
    raster: RasterBuffer,
    is_text: bool,
    speed: int | None,
    energy: int,
    density: int | None,
    blackening: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    feed_padding: int,
    dev_dpi: int,
    can_print_label: bool = False,
    post_print_feed_count: int = 2,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
    protocol_variant: str | None = None,
    page_index: int = 1,
    page_count: int = 1,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> bytes:
    return _build_job_from_raster_set(
        raster_set=RasterSet.from_single(raster),
        is_text=is_text,
        speed=speed,
        energy=energy,
        density=density,
        blackening=blackening,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        feed_padding=feed_padding,
        dev_dpi=dev_dpi,
        can_print_label=can_print_label,
        post_print_feed_count=post_print_feed_count,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_capabilities,
    )


def _build_job_from_raster_set(
    raster_set: RasterSet,
    is_text: bool,
    speed: int | None,
    energy: int,
    density: int | None,
    blackening: int,
    lsb_first: bool,
    protocol_family: ProtocolFamily | str,
    feed_padding: int,
    dev_dpi: int,
    can_print_label: bool = False,
    post_print_feed_count: int = 2,
    image_pipeline: ImagePipelineConfig | None = None,
    paper_mode: PaperMode | None = None,
    protocol_variant: str | None = None,
    page_index: int = 1,
    page_count: int = 1,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> bytes:
    request = _build_request(
        raster_set=raster_set,
        is_text=is_text,
        speed=speed,
        energy=energy,
        density=density,
        blackening=blackening,
        lsb_first=lsb_first,
        protocol_family=protocol_family,
        protocol_variant=protocol_variant,
        feed_padding=feed_padding,
        dev_dpi=dev_dpi,
        can_print_label=can_print_label,
        post_print_feed_count=post_print_feed_count,
        image_pipeline=image_pipeline,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
        runtime_capabilities=runtime_capabilities,
    )
    family_job = _build_family_job(request)
    if family_job is not None:
        return family_job

    job = bytearray()
    job += blackening_cmd(blackening, request.protocol_family)
    job += _build_print_payload_from_raster_set(
        raster_set=raster_set,
        is_text=is_text,
        speed=speed,
        energy=energy,
        lsb_first=lsb_first,
        protocol_family=request.protocol_family,
        protocol_variant=request.protocol_variant,
        can_print_label=can_print_label,
        image_pipeline=request.image_pipeline,
        runtime_capabilities=runtime_capabilities,
    )
    job += feed_paper_cmd(feed_padding, request.protocol_family)
    for _ in range(max(0, request.post_print_feed_count)):
        job += paper_cmd(dev_dpi, request.protocol_family)
    job += feed_paper_cmd(feed_padding, request.protocol_family)
    job += dev_state_cmd(request.protocol_family)
    return bytes(job)
