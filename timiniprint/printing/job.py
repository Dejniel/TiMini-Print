from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..devices.profiles import PrinterProfile
from ..protocol.dynamic_helpers import (
    DensityLevels,
    V5GDynamicHelper,
    V5GDynamicRuntimeContext,
    supports_v5g_d2_status,
    supports_v5g_didian_status,
)
from ..protocol import ImageEncoding, ImagePipelineConfig, PixelFormat, build_job_from_raster_set
from ..protocol.family import ProtocolFamily
from ..protocol.families import get_protocol_definition
from ..rendering.converters import Page, PageLoader
from ..rendering.renderer import apply_page_transforms, image_to_raster_set

DEFAULT_BLACKENING = 3
DEFAULT_FEED_PADDING = 12
DEFAULT_IMAGE_ENERGY = 5000
DEFAULT_TEXT_ENERGY = 8000


@dataclass
class PrintSettings:
    dither: bool = True
    lsb_first: Optional[bool] = None
    text_mode: Optional[bool] = None
    rotate_90_clockwise: bool = False
    text_font: Optional[str] = None
    text_columns: Optional[int] = None
    text_wrap: bool = True
    blackening: int = DEFAULT_BLACKENING
    feed_padding: int = DEFAULT_FEED_PADDING
    trim_side_margins: bool = True
    trim_top_bottom_margins: bool = True
    pdf_pages: Optional[str] = None
    pdf_page_gap_mm: int = 5
    image_encoding_override: Optional[ImageEncoding] = None
    pixel_format_override: Optional[PixelFormat] = None
    v5x_gamma_handle: bool = False
    v5x_gamma_value: Optional[float] = None
    v5c_gamma_handle: bool = True
    v5c_gamma_value: Optional[float] = None


class PrintJobBuilder:
    def __init__(
        self,
        profile: PrinterProfile,
        protocol_family: Optional[ProtocolFamily] = None,
        image_pipeline: Optional[ImagePipelineConfig] = None,
        settings: Optional[PrintSettings] = None,
        page_loader: Optional[PageLoader] = None,
        v5g_dynamic_helper: Optional[V5GDynamicHelper] = None,
        v5g_density_profile: Optional[PrinterProfile] = None,
    ) -> None:
        self.profile = profile
        self.protocol_family = protocol_family or profile.default_protocol_family
        self._explicit_image_pipeline = image_pipeline
        self.settings = settings or PrintSettings()
        self._v5g_dynamic_helper = v5g_dynamic_helper
        self._v5g_density_profile = v5g_density_profile
        pdf_page_gap_px = self._mm_to_px(self.settings.pdf_page_gap_mm, self.profile.dev_dpi)
        self.page_loader = page_loader or PageLoader(
            text_font=self.settings.text_font,
            text_columns=self.settings.text_columns,
            text_wrap=self.settings.text_wrap,
            trim_side_margins=self.settings.trim_side_margins,
            trim_top_bottom_margins=self.settings.trim_top_bottom_margins,
            pdf_pages=self.settings.pdf_pages,
            pdf_page_gap_px=pdf_page_gap_px,
        )
        self.runtime_context = self._build_runtime_context()

    def build_from_file(self, path: str) -> bytes:
        self._validate_input_path(path)
        width = self._normalized_width(self.profile.width)
        pages = self.page_loader.load(path, width)
        pages = apply_page_transforms(pages, rotate_90_clockwise=self.settings.rotate_90_clockwise)
        image_pipeline = self._resolve_image_pipeline()
        required_formats = (image_pipeline.default_format,)
        gamma_handle, gamma_value = self._resolve_gray_preprocessing(image_pipeline)
        data_parts: list[bytes] = []
        for page in pages:
            is_text = self._select_text_mode(page)
            raster_set = image_to_raster_set(
                page.image,
                required_formats,
                dither=self._use_dither(page),
                gamma_handle=gamma_handle,
                gamma_value=gamma_value,
            )
            speed = self.profile.select_speed(is_text=is_text)
            energy = self._select_energy(is_text)
            density = self._select_density(is_text)
            job = build_job_from_raster_set(
                raster_set=raster_set,
                is_text=is_text,
                speed=speed,
                energy=energy,
                density=density,
                blackening=self.settings.blackening,
                lsb_first=self._lsb_first(),
                protocol_family=self.protocol_family,
                feed_padding=self.settings.feed_padding,
                dev_dpi=self.profile.dev_dpi,
                can_print_label=self.profile.can_print_label,
                post_print_feed_count=self.profile.post_print_feed_count,
                image_pipeline=image_pipeline,
            )
            data_parts.append(job)
        return b"".join(data_parts)

    def _resolve_image_pipeline(self) -> ImagePipelineConfig:
        behavior = get_protocol_definition(self.protocol_family).behavior
        if self._explicit_image_pipeline is not None:
            pipeline = self._explicit_image_pipeline
        elif self.protocol_family == self.profile.default_protocol_family:
            pipeline = self.profile.default_image_pipeline
        else:
            pipeline = behavior.default_image_pipeline

        if self.settings.image_encoding_override is not None:
            pipeline = ImagePipelineConfig(
                formats=pipeline.formats,
                encoding=self.settings.image_encoding_override,
            )
        supported_formats = behavior.image_encoding_support.get(pipeline.encoding)
        if supported_formats is None:
            raise ValueError(
                f"{self.protocol_family.value} does not support image encoding {pipeline.encoding.value}"
            )
        if self.settings.pixel_format_override is not None:
            pixel_format = self.settings.pixel_format_override
            if pixel_format not in supported_formats:
                raise ValueError(
                    f"{self.protocol_family.value} image encoding {pipeline.encoding.value} "
                    f"does not support {pixel_format.value}"
                )
            if pixel_format in pipeline.formats:
                pipeline = pipeline.with_default_format(pixel_format)
            else:
                pipeline = ImagePipelineConfig(
                    formats=(pixel_format,) + tuple(
                        value for value in pipeline.formats if value != pixel_format
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
        return pipeline

    def _build_runtime_context(self) -> Optional[V5GDynamicRuntimeContext]:
        if self.protocol_family is not ProtocolFamily.V5G:
            return None
        helper = self._v5g_dynamic_helper
        density_profile = self._density_profile()
        if helper is None or density_profile is None or density_profile.density is None:
            return None
        return V5GDynamicRuntimeContext(
            helper_kind=helper.helper_kind,
            density_profile_key=helper.density_profile_key,
            image_levels=DensityLevels(
                low=density_profile.density.image.low,
                middle=density_profile.density.image.middle,
                high=density_profile.density.image.high,
            ),
            text_levels=DensityLevels(
                low=density_profile.density.text.low,
                middle=density_profile.density.text.middle,
                high=density_profile.density.text.high,
            ),
            applies_d2_status=supports_v5g_d2_status(helper.density_profile_key),
            applies_didian_status=supports_v5g_didian_status(helper.density_profile_key),
        )

    def _density_profile(self) -> Optional[PrinterProfile]:
        if self.protocol_family is not ProtocolFamily.V5G:
            return self.profile
        if self._v5g_density_profile is not None:
            return self._v5g_density_profile
        return self.profile

    def _resolve_gray_preprocessing(
        self,
        image_pipeline: ImagePipelineConfig,
    ) -> tuple[bool, Optional[float]]:
        if self.protocol_family == ProtocolFamily.V5C and image_pipeline.encoding == ImageEncoding.V5C_A5:
            return self.settings.v5c_gamma_handle, self.settings.v5c_gamma_value
        if self.protocol_family == ProtocolFamily.V5X and image_pipeline.encoding == ImageEncoding.V5X_GRAY:
            return self.settings.v5x_gamma_handle, self.settings.v5x_gamma_value
        return False, None

    def _use_dither(self, page: Page) -> bool:
        return self.settings.dither and page.dither

    def _lsb_first(self) -> bool:
        if self.settings.lsb_first is not None:
            return self.settings.lsb_first
        return not self.profile.a4xii

    def _select_text_mode(self, page: Page) -> bool:
        if self.settings.text_mode is not None:
            return self.settings.text_mode
        return page.is_text

    def _select_energy(self, is_text: bool) -> int:
        selected = self.profile.select_energy(
            is_text=is_text,
            blackening=self.settings.blackening,
        )
        if is_text:
            return selected or DEFAULT_TEXT_ENERGY
        return selected or DEFAULT_IMAGE_ENERGY

    def _select_density(self, is_text: bool) -> int | None:
        density_profile = self._density_profile()
        if density_profile is None:
            return None
        return density_profile.select_density(
            is_text=is_text,
            blackening=self.settings.blackening,
        )

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
