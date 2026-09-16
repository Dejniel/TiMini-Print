from __future__ import annotations

from typing import TYPE_CHECKING

from ..raster import PixelFormat, RasterSet
from ._builders import _build_job_model_from_raster_set
from .commands import (
    advance_paper_cmd,
    retract_paper_cmd,
)
from .families import get_protocol_behavior
from .family import ProtocolFamily
from .plan import ProtocolPlan
from .runtime import RuntimePrintCapabilities
from .steps import ProtocolStep
from .types import ImageEncoding, ImagePipelineConfig, PageFlow, PaperMode

if TYPE_CHECKING:
    from ..devices.device import PrinterDevice


class ProtocolJob:
    """Prepared bytes and ordered protocol steps; constructing a job performs no I/O.

    Keep ``steps`` when sending: ``payload`` alone cannot represent reply waits
    or conditional execution. ``payload_segments``, when supplied, must join to
    the supplied payload; otherwise construction raises ``ValueError``. With
    neither payload nor segments, bytes are derived from payload-bearing steps.

    ``wait_for_completion`` requests the runtime's family-specific completion
    policy. It is not evidence that paper has physically finished printing.
    """

    _plan: ProtocolPlan
    payload_segments: tuple[bytes, ...]
    wait_for_completion: bool

    def __init__(
        self,
        payload: bytes | None = None,
        payload_segments: tuple[bytes, ...] = (),
        steps: tuple[ProtocolStep, ...] = (),
        wait_for_completion: bool = False,
    ) -> None:
        normalized_steps = tuple(steps)
        self.wait_for_completion = bool(wait_for_completion)
        if payload_segments:
            self.payload_segments = tuple(bytes(segment) for segment in payload_segments)
            segments_payload = b"".join(self.payload_segments)
            if payload is not None and bytes(payload) != segments_payload:
                raise ValueError("Protocol job payload does not match payload segments")
            normalized_payload = segments_payload
        elif payload is not None:
            normalized_payload = bytes(payload)
            self.payload_segments = (normalized_payload,)
        else:
            self.payload_segments = tuple(
                step.data for step in normalized_steps if step.include_in_payload
            )
            normalized_payload = b"".join(self.payload_segments)
        self._plan = ProtocolPlan(
            payload=normalized_payload,
            steps=normalized_steps,
        )

    @property
    def payload(self) -> bytes:
        """Return concatenated payload bytes, without the semantics of reply steps."""
        return self._plan.payload

    @property
    def steps(self) -> tuple[ProtocolStep, ...]:
        """Return ordered execution steps, including any queries and waits."""
        return self._plan.steps


class PrinterProtocol:
    """Build jobs and inspect recipe capabilities without connecting or sending.

    Use the device returned by ``ConnectedPrinter.printer_device()`` after
    connection when geometry or the protocol variant requires negotiation.
    This class cannot resolve an unprobed printer by itself.
    """

    def __init__(self, device: PrinterDevice) -> None:
        self.device = device

    def build_job(
        self,
        raster_set: RasterSet,
        *,
        is_text: bool,
        blackening: int = 3,
        feed_padding: int = 0,
        paper_preset_key: str | None = None,
        paper_mode: PaperMode | None = None,
        lsb_first: bool | None = None,
        image_pipeline: ImagePipelineConfig | None = None,
        image_encoding_override: ImageEncoding | None = None,
        pixel_format_override: PixelFormat | None = None,
        page_index: int = 1,
        page_count: int = 1,
        page_flow: PageFlow = PageFlow.PAGED,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> ProtocolJob:
        """Encode an already rendered raster into a job; do not send or rasterize it.

        ``paper_preset_key`` selects geometry and a media recipe. If omitted,
        use the first preset for an explicit ``paper_mode``, or the profile's
        default preset. ``paper_mode`` is a low-level recipe override, not a
        darkness or width setting; normal integrations should select a preset.

        Page indices are one-based. ``PAGED`` treats pages as separate media
        pages; ``CONTINUOUS`` keeps intermediate render fragments in one flow.
        Pass session capabilities when encoding depends on negotiated data.

        Invalid paper, raster or codec combinations raise ``ValueError``;
        recipes needing missing runtime data may raise ``RuntimeError``.
        """
        if paper_preset_key is not None:
            paper_preset = self.device.profile.paper_preset(paper_preset_key)
            if paper_preset is None:
                raise ValueError(
                    f"{self.device.display_name or self.device.profile_key} does not support paper "
                    f"{paper_preset_key!r}"
                )
        else:
            paper_preset = self.device.profile.paper_preset_for_mode(paper_mode)
        resolved_pipeline = self.resolve_image_pipeline(
            paper_preset_key=paper_preset.key,
            paper_mode=paper_mode,
            image_pipeline=image_pipeline,
            image_encoding_override=image_encoding_override,
            pixel_format_override=pixel_format_override,
            runtime_capabilities=runtime_capabilities,
        )
        resolved_paper_mode = (
            paper_mode if paper_mode is not None else paper_preset.paper_mode
        )
        runtime_density = (
            None
            if self.device.runtime_settings is None
            else self.device.runtime_settings.select_density(
                is_text=is_text,
                blackening=blackening,
            )
        )
        payload, steps = _build_job_model_from_raster_set(
            image_energy=self.device.profile.select_energy(is_text=False, blackening=blackening),
            back_paper_num=self.device.profile.back_paper_num,
            raster_set=raster_set,
            is_text=is_text,
            speed=self.device.profile.select_speed(is_text=is_text),
            energy=self.device.profile.select_energy(
                is_text=is_text,
                blackening=blackening,
            ),
            density=runtime_density
            if runtime_density is not None
            else self.device.profile.select_density(
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
            left_padding_pixels=paper_preset.left_padding_px,
            one_length=self.device.profile.one_length,
            a4xii=self.device.profile.a4xii,
            a4_sheet_max_height=paper_preset.max_height_px,
            image_pipeline=resolved_pipeline,
            paper_mode=resolved_paper_mode,
            page_index=page_index,
            page_count=page_count,
            page_flow=page_flow,
            runtime_capabilities=runtime_capabilities,
        )
        return ProtocolJob(
            payload=payload,
            steps=steps,
            wait_for_completion=page_flow == PageFlow.PAGED or page_index >= page_count,
        )

    def build_paper_motion(self, action: str) -> ProtocolJob:
        """Build, but do not send, one ``"feed"`` or ``"retract"`` operation.

        Distance and framing belong to the printer recipe, not a universal
        millimetre setting. Unknown actions raise ``ValueError``; unsupported
        recipes may raise ``ValueError``/``NotImplementedError`` or return an
        empty job. Use ``supports_paper_motion()`` to decide whether to offer it.
        """
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
        return ProtocolJob(payload=payload)

    def resolve_image_pipeline(
        self,
        *,
        paper_preset_key: str | None = None,
        paper_mode: PaperMode | None = None,
        image_pipeline: ImagePipelineConfig | None = None,
        image_encoding_override: ImageEncoding | None = None,
        pixel_format_override: PixelFormat | None = None,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> ImagePipelineConfig:
        """Select input formats and a wire encoding without I/O or image conversion.

        Start with ``image_pipeline`` if supplied, otherwise the device default
        (or family default when its protocol differs from the profile). An
        explicit ``image_encoding_override`` replaces that encoding.

        A ``pixel_format_override`` alone may select another compatible codec.
        With an explicit pipeline or encoding, an incompatible format instead
        raises ``ValueError``. Without a format override, retain the default
        format when supported, otherwise choose a supported fallback.

        Paper defaults to the profile preset; ``paper_mode`` overrides its
        recipe. When selecting by key, unknown keys raise ``ValueError``, as do
        unsupported encodings.
        Family runtime fallbacks are applied last. ``runtime_capabilities=None``
        means no session-derived restrictions, not a negative capability reply.
        """
        behavior = get_protocol_behavior(self.device.protocol_family)
        if image_pipeline is not None:
            pipeline = image_pipeline
        elif self.device.protocol_family == self.device.profile.protocol_default.type:
            pipeline = self.device.image_pipeline
        else:
            pipeline = behavior.default_image_pipeline

        if image_encoding_override is not None:
            pipeline = ImagePipelineConfig(
                formats=pipeline.formats,
                encoding=image_encoding_override,
            )
        image_encoding_support = behavior.image_encoding_support_for(
            self.device.protocol_variant, paper_mode or self._paper_mode(paper_preset_key),
        )
        if pixel_format_override is not None and image_encoding_override is None and image_pipeline is None:
            if pixel_format_override not in image_encoding_support.get(pipeline.encoding, ()):
                for encoding, formats in image_encoding_support.items():
                    if pixel_format_override in formats:
                        pipeline = ImagePipelineConfig(formats=formats, encoding=encoding)
                        break
        supported_formats = image_encoding_support.get(pipeline.encoding)
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
        allowed_formats = {fmt for formats in image_encoding_support.values() for fmt in formats}
        pipeline = ImagePipelineConfig(
            formats=tuple(fmt for fmt in pipeline.formats if fmt in allowed_formats),
            encoding=pipeline.encoding,
        )
        return self._apply_runtime_capabilities(pipeline, runtime_capabilities)

    def _paper_mode(self, paper_preset_key: str | None) -> PaperMode | None:
        if paper_preset_key is None:
            return self.device.profile.default_paper_preset.paper_mode
        preset = self.device.profile.paper_preset(paper_preset_key)
        if preset is None:
            raise ValueError(f"{self.device.profile_key} does not support paper {paper_preset_key!r}")
        return preset.paper_mode

    def supported_paper_modes(self) -> tuple[PaperMode, ...]:
        """Return this variant's supported media recipes without querying hardware.

        These are not paper sizes. Use the resolved device's ``paper_presets``
        for user-facing choices, since several presets can share one mode.
        """
        behavior = get_protocol_behavior(self.device.protocol_family)
        return behavior.supported_paper_modes_for(self.device.protocol_variant)

    def supported_pixel_formats(
        self, *, runtime_capabilities: RuntimePrintCapabilities | None = None,
        paper_preset_key: str | None = None,
    ) -> tuple[PixelFormat, ...]:
        """Return selectable input formats, effective default first, without I/O.

        The tuple is deduplicated and includes formats requiring another codec.
        ``paper_preset_key=None`` uses the profile default; recompute when the
        selected paper changes. Pass ``ConnectedPrinter.print_capabilities()``
        to exclude choices rejected by known runtime fallbacks; ``None`` applies
        only catalog/recipe rules. Unknown paper or invalid pipeline choices
        raise ``ValueError``. Results describe implemented options, not a probe.
        """
        behavior = get_protocol_behavior(self.device.protocol_family)
        support = behavior.image_encoding_support_for(self.device.protocol_variant, self._paper_mode(paper_preset_key))
        default = self.resolve_image_pipeline(
            paper_preset_key=paper_preset_key, runtime_capabilities=runtime_capabilities,
        ).default_format
        formats = dict.fromkeys((default, *(fmt for values in support.values() for fmt in values)))
        return tuple(
            fmt for fmt in formats
            if self.resolve_image_pipeline(
                paper_preset_key=paper_preset_key,
                pixel_format_override=fmt, runtime_capabilities=runtime_capabilities,
            ).default_format == fmt
        )

    def supported_print_settings(
        self, *, pixel_format: PixelFormat | None = None,
        paper_preset_key: str | None = None,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> tuple[str, ...]:
        """Return implemented adjustment keys for this paper/format, without I/O.

        The result contains ``"blackening"`` and/or ``"text_mode"``, or is empty.
        Profile-mapped density/energy controls require variable blackening
        levels. A negative runtime blackening capability removes that control.
        Rendering options such as rotation and dithering are not listed here.

        ``pixel_format=None`` and ``paper_preset_key=None`` use device defaults.
        Pass the same paper, format and session capabilities used for printing;
        invalid combinations raise ``ValueError`` through pipeline resolution.
        This describes available controls, not validation of all PrintSettings.
        """
        pipeline = self.resolve_image_pipeline(
            paper_preset_key=paper_preset_key,
            pixel_format_override=pixel_format, runtime_capabilities=runtime_capabilities,
        )
        behavior = get_protocol_behavior(self.device.protocol_family)
        if behavior.print_controls_resolver is not None:
            settings = behavior.print_controls_resolver(self.device.protocol_variant, pipeline.encoding)
        else:
            settings = behavior.print_controls
        controls = set(settings)
        for control in ("density", "energy"):
            if control not in controls:
                continue
            for is_text in (False, True):
                values = []
                for level in range(1, 6):
                    if control == "energy":
                        value = self.device.profile.select_energy(is_text=is_text, blackening=level)
                    else:
                        runtime = self.device.runtime_settings
                        value = runtime.select_density(is_text=is_text, blackening=level) if runtime else None
                        if value is None:
                            value = self.device.profile.select_density(is_text=is_text, blackening=level)
                    values.append(value)
                if len(set(values)) > 1:
                    controls.add("blackening")
        settings = tuple(value for value in ("blackening", "text_mode") if value in controls)
        if runtime_capabilities is not None and runtime_capabilities.supports_blackening is False:
            settings = tuple(value for value in settings if value != "blackening")
        return settings

    def supports_paper_motion(self, action: str) -> bool:
        """Return whether the recipe builds a nonempty manual motion job, without I/O.

        ``action`` must be ``"feed"`` or ``"retract"``; other values raise
        ``ValueError``. Unsupported/empty recipes return ``False``. ``True``
        does not mean the printer is connected, ready, or has paper loaded.
        """
        if action not in ("feed", "retract"):
            raise ValueError(f"Unknown paper motion action: {action}")
        try:
            job = self.build_paper_motion(action)
        except (ValueError, NotImplementedError):
            return False
        return bool(job.payload or job.steps)


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
