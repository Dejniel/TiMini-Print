from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Mapping

from ....raster import PixelFormat, RasterBuffer
from ...family import ProtocolFamily
from ...plan import ProtocolPlan
from ...steps import ProtocolStep
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from ..bitmap import build_1f10_zlib_raster, build_gs_v0_single_raster, packed_raster_dimensions_le16

from .transactions import (
    NORMAL_FINALIZE_TIMEOUT_SEC, density_setting, finalize, paper_setting, speed_setting, status_query,
)


class LuckNormalPaperMode(IntEnum):
    """Paper-mode values for the Luck normal-family transport."""

    PLAIN = 16
    TAG = 32
    CIRCLE_TAG = 33
    FOLDER = 48
    TATTOO = 64
    BLACK_TAG = 80


class LuckNormalPageMarkerFlow(str, Enum):
    """Page-boundary marker sequences used by Luck normal-family recipes."""

    NONE = "none"
    LAST_ONLY = "last_only"
    FIRST_MIDDLE_LAST = "first_middle_last"


@dataclass(frozen=True)
class LuckNormalModeRecipe:
    """One media-specific print recipe for the Luck normal-family stack.

    `paper_type_stage` controls when `setPaperType(...)` is emitted relative to
    enable and wakeup. `adjust_*_scope` limits position-adjust commands to
    `never`, `always`, `first_page`, or `last_page`. `page_marker_flow`
    controls the distinct first/intermediate/last page marker lifecycle.
    A last-page feed follows positioning; the final marker precedes any final
    position adjustment. Unacknowledged finalization is explicit per medium.
    """

    paper_mode: LuckNormalPaperMode | None = None
    paper_type_stage: str = "after_wakeup"
    finish_action: str = "line_feed"
    adjust_before: int | None = None
    adjust_before_scope: str = "never"
    adjust_after: int | None = None
    adjust_after_scope: str = "never"
    page_marker_flow: LuckNormalPageMarkerFlow = LuckNormalPageMarkerFlow.NONE
    wait_for_paper_reply: bool = True
    set_raster_width: bool = False
    last_page_feed_dots: int = 0
    wait_for_finalize_reply: bool = True


@dataclass(frozen=True)
class LuckNormalCommandDialect:
    """Byte-level command dialect for the Luck normal-family transport."""

    enable_command: bytes
    finalize_command: bytes
    wakeup_command: bytes = bytes(12)
    position_command: bytes = bytes([0x1D, 0x0C])
    reverse_feed_command: bytes = bytes([0x1F, 0x11, 0x11])

    def set_paper_type(self, paper_class: int, paper_mode: int) -> bytes:
        return bytes([0x1F, 0x80, paper_class & 0xFF, paper_mode & 0xFF])

    def set_density(self, density: int) -> bytes:
        return bytes([0x10, 0xFF, 0x10, 0x00, density & 0xFF])

    def set_speed(self, speed: int) -> bytes:
        return bytes([0x10, 0xFF, 0xC0, speed])

    def line_feed(self, dots: int) -> bytes:
        return bytes([0x1B, 0x4A, dots & 0xFF])

    def reverse_feed(self, dots: int) -> bytes:
        return self.reverse_feed_command + bytes([dots & 0xFF])

    def set_width(self, dots: int) -> bytes:
        return b"\x10\xff\x15" + dots.to_bytes(2, "little")

    def adjust_position_auto(self, marker: int) -> bytes:
        return bytes([0x1F, 0x11, marker & 0xFF])

    def mark_first(self) -> bytes:
        return bytes([0x1B, 0xBB, 0xCC])

    def mark_last(self) -> bytes:
        return bytes([0x1B, 0xBB, 0xBB])

    def mark_not_last(self) -> bytes:
        return bytes([0x1B, 0xBB, 0xAA])


LUCK_NORMAL_DIALECT = LuckNormalCommandDialect(
    enable_command=bytes([0x10, 0xFF, 0xF1, 0x03]),
    finalize_command=bytes([0x10, 0xFF, 0xF1, 0x45]),
)

LUCK_NORMAL_MODE2_DIALECT = LuckNormalCommandDialect(
    # QIRUI-branded L1 variants switch the printer into enable mode 2.
    enable_command=bytes([0x10, 0xFF, 0xF1, 0x02]),
    finalize_command=bytes([0x10, 0xFF, 0xF1, 0x45]),
)


class LuckNormalBitmapEncoder:
    """Encode raster data into the Luck normal-family bitmap payloads."""

    def encode(
        self,
        request: PrintJobRequest,
        *,
        gray_level_override: int | None = None,
    ) -> bytes:
        encoding = request.image_pipeline.encoding
        if encoding == ImageEncoding.LUCK_NORMAL_RAW:
            return self._encode_raw(request.require_raster(PixelFormat.BW1))
        if encoding == ImageEncoding.LUCK_NORMAL_GRAY:
            return self._encode_gray(
                request.default_raster,
                self._gray_level_for_request(request, gray_level_override),
            )
        if encoding == ImageEncoding.LUCK_NORMAL_COMPRESSED:
            return self._encode_compressed(request.require_raster(PixelFormat.BW1))
        raise ValueError(f"Unsupported Luck normal image encoding: {encoding.value}")

    def _encode_raw(self, raster: RasterBuffer) -> bytes:
        return build_gs_v0_single_raster(raster)

    def _encode_compressed(self, raster: RasterBuffer) -> bytes:
        return build_1f10_zlib_raster(raster)

    def _encode_gray(self, raster: RasterBuffer, gray_level: int) -> bytes:
        return (
            b"\x1d\x47\x59" + bytes([gray_level]) + packed_raster_dimensions_le16(raster)
            + self._pack_gray_rows(raster, gray_level)
        )

    def _pack_gray_rows(self, raster: RasterBuffer, gray_level: int) -> bytes:
        levels = self._gray_levels(raster, gray_level)
        packed = bytearray()
        for row in range(raster.height):
            row_start = row * raster.width
            for column in range(0, raster.width, 2):
                high = levels[row_start + column]
                low = levels[row_start + column + 1] if column + 1 < raster.width else 0
                packed.append(((high & 0x0F) << 4) | (low & 0x0F))
        return bytes(packed)

    def _gray_levels(self, raster: RasterBuffer, gray_level: int) -> list[int]:
        max_level = max(0, gray_level - 1)
        pixels = list(raster.pixels)
        if raster.pixel_format == PixelFormat.GRAY4:
            if max_level == 15:
                return [max(0, min(15, int(value))) for value in pixels]
            return [max(0, min(max_level, (int(value) * max_level) // 15)) for value in pixels]
        if raster.pixel_format == PixelFormat.GRAY8:
            return [
                max_level - min(max_level, (int(value) * gray_level) // 256)
                for value in pixels
            ]
        raise ValueError("Luck normal gray jobs require GRAY4 or GRAY8 raster data")

    @staticmethod
    def _gray_level_for_request(
        request: PrintJobRequest,
        variant_override: int | None,
    ) -> int:
        runtime_capabilities = request.runtime_capabilities
        if runtime_capabilities is not None and runtime_capabilities.gray_level_override is not None:
            return runtime_capabilities.gray_level_override
        if variant_override is not None:
            return variant_override
        return 16


@dataclass(frozen=True)
class LuckNormalFamilyRecipe:
    """Object-oriented recipe for one Luck normal-family protocol branch."""

    protocol_family: ProtocolFamily
    default_image_pipeline: ImagePipelineConfig
    image_encoding_support: Mapping[ImageEncoding, tuple[PixelFormat, ...]]
    default_paper_mode: PaperMode = PaperMode.PLAIN
    mode_recipes: Mapping[PaperMode, LuckNormalModeRecipe] = field(default_factory=dict)
    end_line_dots_200dpi: int = 80
    end_line_dots_300dpi: int = 120
    dialect: LuckNormalCommandDialect = LUCK_NORMAL_DIALECT
    bitmap_encoder: LuckNormalBitmapEncoder = field(default_factory=LuckNormalBitmapEncoder)
    variants: Mapping[str, "LuckNormalVariantRecipe"] = field(default_factory=dict)

    def build_behavior(self) -> ProtocolBehavior:
        """Bind all protocol operations to this recipe, including its variants."""
        return ProtocolBehavior(
            print_controls=("blackening",),
            default_image_pipeline=self.default_image_pipeline,
            image_encoding_support=self.image_encoding_support,
            image_encoding_support_resolver=self.image_encoding_support_for_variant,
            supported_protocol_variants=self.supported_variants(),
            supported_paper_modes=self.supported_paper_modes(),
            supported_paper_modes_resolver=self.supported_paper_modes,
            advance_paper_builder=self.build_advance_paper,
            retract_paper_builder=self.build_retract_paper,
            job_builder=self.build_job,
        )

    def build_job(self, request: PrintJobRequest) -> ProtocolPlan:
        return ProtocolPlan.sequence(tuple(self.build_steps(request)))

    def build_steps(self, request: PrintJobRequest) -> list[ProtocolStep]:
        recipe = self.recipe_for_mode(request.paper_mode, request.protocol_variant)
        dialect = self.dialect_for_variant(request.protocol_variant)
        variant = self._variant(request.protocol_variant)
        steps: list[ProtocolStep] = []
        if request.density is not None:
            steps.append(density_setting(dialect.set_density(request.density)))
        if variant is not None and variant.speed_range is not None and request.speed is not None:
            minimum, maximum = variant.speed_range
            if not minimum <= request.speed <= maximum:
                raise ValueError(f"Luck speed must be in {minimum}..{maximum}")
            steps.append(speed_setting(dialect.set_speed(request.speed)))
        steps.append(status_query())
        if recipe.paper_mode is not None and recipe.paper_type_stage == "before_enable":
            steps.append(
                paper_setting(
                    dialect.set_paper_type(1, int(recipe.paper_mode)),
                    wait_for_reply=recipe.wait_for_paper_reply,
                )
            )
        steps.append(ProtocolStep.send("enable", dialect.enable_command))
        steps.append(ProtocolStep.send("wakeup", dialect.wakeup_command))
        if recipe.paper_mode is not None and recipe.paper_type_stage == "after_wakeup":
            steps.append(
                paper_setting(
                    dialect.set_paper_type(1, int(recipe.paper_mode)),
                    wait_for_reply=recipe.wait_for_paper_reply,
                )
            )
        elif recipe.paper_mode is not None and recipe.paper_type_stage != "before_enable":
            raise ValueError(
                f"Unsupported Luck normal paper type stage: {recipe.paper_type_stage}"
            )
        if self._should_run_scope(recipe.adjust_before_scope, request) and recipe.adjust_before is not None:
            steps.append(ProtocolStep.send("adjust before", dialect.adjust_position_auto(recipe.adjust_before)))
        if (
            recipe.page_marker_flow is LuckNormalPageMarkerFlow.FIRST_MIDDLE_LAST
            and request.is_first_page
        ):
            steps.append(ProtocolStep.send("mark first", dialect.mark_first()))
        if recipe.set_raster_width:
            steps.append(ProtocolStep.send("raster width", dialect.set_width(request.default_raster.width)))
        steps.append(
            ProtocolStep.send(
                "bitmap",
                self.bitmap_encoder.encode(
                    request,
                    gray_level_override=self.gray_level_override_for_variant(
                        request.protocol_variant
                    ),
                ),
            )
        )
        if request.ends_media_page:
            if recipe.finish_action == "position":
                steps.append(ProtocolStep.send("position", dialect.position_command))
            elif recipe.finish_action == "line_feed":
                steps.append(
                    ProtocolStep.send(
                        "line feed",
                        dialect.line_feed(self.end_line_dots_for_request(request)),
                    )
                )
            elif recipe.finish_action == "none":
                pass
            else:
                raise ValueError(f"Unsupported Luck normal finish action: {recipe.finish_action}")
        if request.ends_media_page and request.is_last_page and recipe.last_page_feed_dots:
            steps.append(ProtocolStep.send("last page feed", dialect.line_feed(recipe.last_page_feed_dots)))
        if (
            recipe.page_marker_flow
            in {
                LuckNormalPageMarkerFlow.LAST_ONLY,
                LuckNormalPageMarkerFlow.FIRST_MIDDLE_LAST,
            }
            and request.is_last_page
        ):
            steps.append(ProtocolStep.send("mark last", dialect.mark_last()))
        elif recipe.page_marker_flow is LuckNormalPageMarkerFlow.FIRST_MIDDLE_LAST:
            steps.append(ProtocolStep.send("mark not last", dialect.mark_not_last()))
        if self._should_run_scope(recipe.adjust_after_scope, request) and recipe.adjust_after is not None:
            steps.append(ProtocolStep.send("adjust after", dialect.adjust_position_auto(recipe.adjust_after)))
        timeout = variant.finalize_timeout_sec if variant is not None else NORMAL_FINALIZE_TIMEOUT_SEC
        steps.append(finalize(dialect.finalize_command, timeout_sec=timeout) if recipe.wait_for_finalize_reply
                     else ProtocolStep.send("finalize", dialect.finalize_command))
        return steps

    def build_advance_paper(
        self,
        dpi: int,
        _protocol_family: ProtocolFamily,
        protocol_variant: str | None = None,
    ) -> bytes:
        return self.dialect_for_variant(protocol_variant).line_feed(
            self.end_line_dots_for_dpi(dpi, protocol_variant)
        )

    def build_retract_paper(
        self,
        dpi: int,
        _protocol_family: ProtocolFamily,
        protocol_variant: str | None = None,
    ) -> bytes:
        return self.dialect_for_variant(protocol_variant).reverse_feed(
            self.end_line_dots_for_dpi(dpi, protocol_variant)
        )

    def dialect_for_variant(self, protocol_variant: str | None) -> LuckNormalCommandDialect:
        variant = self._variant(protocol_variant)
        if variant is not None and variant.dialect is not None:
            return variant.dialect
        return self.dialect

    def end_line_dots_for_request(self, request: PrintJobRequest) -> int:
        return self.end_line_dots_for_dpi(request.dev_dpi, request.protocol_variant)

    def end_line_dots_for_dpi(self, dpi: int, protocol_variant: str | None = None) -> int:
        variant = self._variant(protocol_variant)
        if int(dpi) == 300:
            if variant is not None and variant.end_line_dots_300dpi is not None:
                return variant.end_line_dots_300dpi
            return self.end_line_dots_300dpi
        if variant is not None and variant.end_line_dots_200dpi is not None:
            return variant.end_line_dots_200dpi
        return self.end_line_dots_200dpi

    def supported_paper_modes(self, protocol_variant: str | None = None) -> tuple[PaperMode, ...]:
        return tuple(self.mode_recipes_for_variant(protocol_variant))

    def supported_variants(self) -> tuple[str, ...]:
        return tuple(self.variants)

    def image_encoding_support_for_variant(
        self, protocol_variant: str | None, _paper_mode: PaperMode | None = None,
    ) -> Mapping[ImageEncoding, tuple[PixelFormat, ...]]:
        variant = self._variant(protocol_variant)
        if variant is not None and variant.image_encoding_support is not None:
            return variant.image_encoding_support
        return self.image_encoding_support

    def recipe_for_mode(
        self,
        paper_mode: PaperMode | None,
        protocol_variant: str | None = None,
    ) -> LuckNormalModeRecipe:
        recipes = self.mode_recipes_for_variant(protocol_variant)
        default_paper_mode = self.default_paper_mode_for_variant(protocol_variant)
        mode = paper_mode or default_paper_mode
        try:
            return recipes[mode]
        except KeyError as exc:
            raise ValueError(
                f"{self.protocol_family.value} does not support paper mode {mode.value}"
            ) from exc

    def mode_recipes_for_variant(
        self,
        protocol_variant: str | None,
    ) -> Mapping[PaperMode, LuckNormalModeRecipe]:
        variant = self._variant(protocol_variant)
        if variant is not None and variant.mode_recipes is not None:
            return variant.mode_recipes
        return self.mode_recipes

    def default_paper_mode_for_variant(self, protocol_variant: str | None) -> PaperMode:
        variant = self._variant(protocol_variant)
        if variant is not None and variant.default_paper_mode is not None:
            return variant.default_paper_mode
        return self.default_paper_mode

    def gray_level_override_for_variant(self, protocol_variant: str | None) -> int | None:
        variant = self._variant(protocol_variant)
        if variant is None:
            return None
        return variant.gray_level_override

    def _variant(self, protocol_variant: str | None) -> "LuckNormalVariantRecipe | None":
        if protocol_variant in (None, ""):
            return None
        try:
            return self.variants[protocol_variant]
        except KeyError as exc:
            raise ValueError(
                f"{self.protocol_family.value} does not support protocol variant {protocol_variant!r}"
            ) from exc

    @staticmethod
    def _should_run_scope(scope: str, request: PrintJobRequest) -> bool:
        if scope == "never":
            return False
        if scope == "always":
            return True
        if scope == "first_page":
            return request.is_first_page
        if scope == "last_page":
            return request.is_last_page
        raise ValueError(f"Unsupported Luck normal adjust scope: {scope}")


@dataclass(frozen=True)
class LuckNormalVariantRecipe:
    """Protocol-level overrides for one named Luck normal variant.

    A variant can replace the command dialect, media recipes, default paper
    mode, or line-feed defaults without changing the parent protocol family.
    """

    dialect: LuckNormalCommandDialect | None = None
    mode_recipes: Mapping[PaperMode, LuckNormalModeRecipe] | None = None
    default_paper_mode: PaperMode | None = None
    end_line_dots_200dpi: int | None = None
    end_line_dots_300dpi: int | None = None
    gray_level_override: int | None = None
    finalize_timeout_sec: float = NORMAL_FINALIZE_TIMEOUT_SEC
    speed_range: tuple[int, int] | None = None
    image_encoding_support: Mapping[ImageEncoding, tuple[PixelFormat, ...]] | None = None


LUCK_NORMAL_MONO_IMAGE_SUPPORT: Mapping[ImageEncoding, tuple[PixelFormat, ...]] = {
    ImageEncoding.LUCK_NORMAL_RAW: (PixelFormat.BW1,),
    ImageEncoding.LUCK_NORMAL_COMPRESSED: (PixelFormat.BW1,),
}

LUCK_NORMAL_IMAGE_SUPPORT: Mapping[ImageEncoding, tuple[PixelFormat, ...]] = {
    **LUCK_NORMAL_MONO_IMAGE_SUPPORT,
    ImageEncoding.LUCK_NORMAL_GRAY: (PixelFormat.GRAY4, PixelFormat.GRAY8),
}
