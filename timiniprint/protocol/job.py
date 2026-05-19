from __future__ import annotations

from typing import TYPE_CHECKING

from ..printing.runtime.base import RuntimePrintCapabilities
from ..raster import PixelFormat, RasterSet
from ._builders import _build_job_model_from_raster_set
from .commands import (
    advance_paper_cmd,
    retract_paper_cmd,
)
from .families import get_protocol_behavior
from .family import ProtocolFamily
from .steps import ProtocolStep
from .types import ImageEncoding, ImagePipelineConfig, PaperMode

if TYPE_CHECKING:
    from ..devices.device import PrinterDevice
    from ..printing.runtime.base import RuntimeController


class ProtocolJob:
    """Printable protocol payload plus optional session runtime controller."""

    _payload: bytes | None
    runtime_controller: RuntimeController | None
    payload_segments: tuple[bytes, ...]
    steps: tuple[ProtocolStep, ...]

    def __init__(
        self,
        payload: bytes | None = None,
        runtime_controller: RuntimeController | None = None,
        payload_segments: tuple[bytes, ...] = (),
        steps: tuple[ProtocolStep, ...] = (),
    ) -> None:
        self._payload = payload
        self.runtime_controller = runtime_controller
        self.steps = tuple(steps)
        if payload_segments:
            self.payload_segments = tuple(bytes(segment) for segment in payload_segments)
        elif payload is not None:
            self.payload_segments = (payload,)
        else:
            self.payload_segments = tuple(step.data for step in self.steps if step.include_in_payload)

    @property
    def payload(self) -> bytes:
        if self._payload is None:
            self._payload = b"".join(self.payload_segments)
        return self._payload


class PrinterProtocol:
    """Build protocol jobs for one resolved ``PrinterDevice``."""

    def __init__(self, device: PrinterDevice) -> None:
        self.device = device

    def create_runtime_controller(self) -> RuntimeController | None:
        """Create the session runtime controller required by this device, if any."""
        from ..printing.runtime.factory import runtime_controller_for_device

        return runtime_controller_for_device(self.device)

    def build_job(
        self,
        raster_set: RasterSet,
        *,
        is_text: bool,
        blackening: int = 3,
        feed_padding: int = 0,
        paper_mode: PaperMode | None = None,
        lsb_first: bool | None = None,
        image_pipeline: ImagePipelineConfig | None = None,
        image_encoding_override: ImageEncoding | None = None,
        pixel_format_override: PixelFormat | None = None,
        page_index: int = 1,
        page_count: int = 1,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
        runtime_controller: RuntimeController | None = None,
    ) -> ProtocolJob:
        """Build a printable job from raster input for this device."""
        resolved_pipeline = self.resolve_image_pipeline(
            image_pipeline=image_pipeline,
            image_encoding_override=image_encoding_override,
            pixel_format_override=pixel_format_override,
            runtime_capabilities=runtime_capabilities,
        )
        resolved_paper_mode = (
            paper_mode if paper_mode is not None else self.device.profile.default_paper_mode
        )
        density_profile = self.device.runtime_density_profile or self.device.profile
        payload, steps = _build_job_model_from_raster_set(
            raster_set=raster_set,
            is_text=is_text,
            speed=self.device.profile.select_speed(is_text=is_text),
            energy=self.device.profile.select_energy(
                is_text=is_text,
                blackening=blackening,
            ),
            density=density_profile.select_density(
                is_text=is_text,
                blackening=blackening,
            ),
            blackening=blackening,
            lsb_first=lsb_first if lsb_first is not None else not self.device.profile.a4xii,
            protocol_family=self.device.protocol_family,
            protocol_variant=self.device.protocol_variant,
            feed_padding=feed_padding,
            dev_dpi=self.device.profile.dev_dpi,
            can_print_label=self.device.profile.can_print_label,
            post_print_feed_count=self.device.profile.post_print_feed_count,
            image_pipeline=resolved_pipeline,
            paper_mode=resolved_paper_mode,
            page_index=page_index,
            page_count=page_count,
            runtime_capabilities=runtime_capabilities,
        )
        return ProtocolJob(
            payload=payload,
            runtime_controller=runtime_controller or self.create_runtime_controller(),
            steps=steps,
        )

    def build_paper_motion(self, action: str) -> ProtocolJob:
        """Build a feed or retract paper-motion job for this device."""
        if action == "feed":
            payload = advance_paper_cmd(
                self.device.profile.dev_dpi,
                self.device.protocol_family,
                self.device.protocol_variant,
            )
        elif action == "retract":
            payload = retract_paper_cmd(
                self.device.profile.dev_dpi,
                self.device.protocol_family,
                self.device.protocol_variant,
            )
        else:
            raise ValueError(f"Unknown paper motion action: {action}")
        return ProtocolJob(payload=payload, runtime_controller=None)

    def resolve_image_pipeline(
        self,
        *,
        image_pipeline: ImagePipelineConfig | None = None,
        image_encoding_override: ImageEncoding | None = None,
        pixel_format_override: PixelFormat | None = None,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> ImagePipelineConfig:
        """Resolve the effective image pipeline for this job request."""
        behavior = get_protocol_behavior(self.device.protocol_family)
        if image_pipeline is not None:
            pipeline = image_pipeline
        elif self.device.protocol_family == self.device.profile.default_protocol_family:
            pipeline = self.device.image_pipeline
        else:
            pipeline = behavior.default_image_pipeline

        if image_encoding_override is not None:
            pipeline = ImagePipelineConfig(
                formats=pipeline.formats,
                encoding=image_encoding_override,
            )
        supported_formats = behavior.image_encoding_support.get(pipeline.encoding)
        if supported_formats is None:
            raise ValueError(
                f"{self.device.protocol_family.value} does not support image encoding {pipeline.encoding.value}"
            )
        if pixel_format_override is not None:
            if pixel_format_override not in supported_formats:
                raise ValueError(
                    f"{self.device.protocol_family.value} image encoding {pipeline.encoding.value} "
                    f"does not support {pixel_format_override.value}"
                )
            if pixel_format_override in pipeline.formats:
                pipeline = pipeline.with_default_format(pixel_format_override)
            else:
                pipeline = ImagePipelineConfig(
                    formats=(pixel_format_override,) + tuple(
                        value for value in pipeline.formats if value != pixel_format_override
                    ),
                    encoding=pipeline.encoding,
                )
        elif pipeline.default_format not in supported_formats:
            fallback = next((value for value in pipeline.formats if value in supported_formats), None)
            if fallback is not None:
                pipeline = pipeline.with_default_format(fallback)
            else:
                pipeline = ImagePipelineConfig(
                    formats=tuple(supported_formats) + tuple(
                        value for value in pipeline.formats if value not in supported_formats
                    ),
                    encoding=pipeline.encoding,
                )
        return self._apply_runtime_capabilities(pipeline, runtime_capabilities)

    def supported_paper_modes(self) -> tuple[PaperMode, ...]:
        """Return user-selectable paper modes supported by this device recipe."""
        behavior = get_protocol_behavior(self.device.protocol_family)
        if behavior.supported_paper_modes_resolver is not None:
            return behavior.supported_paper_modes_resolver(self.device.protocol_variant)
        return behavior.supported_paper_modes

    @staticmethod
    def _apply_runtime_capabilities(
        pipeline: ImagePipelineConfig,
        runtime_capabilities: RuntimePrintCapabilities | None,
    ) -> ImagePipelineConfig:
        if runtime_capabilities is None:
            return pipeline
        if (
            pipeline.encoding == ImageEncoding.LUCK_NORMAL_GRAY
            and runtime_capabilities.supports_gray is False
        ):
            return ImagePipelineConfig(
                formats=(PixelFormat.BW1,) + tuple(
                    value for value in pipeline.formats if value is not PixelFormat.BW1
                ),
                encoding=ImageEncoding.LUCK_NORMAL_RAW,
            )
        return pipeline
