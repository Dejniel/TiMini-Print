from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Mapping

from ....raster import PixelFormat, RasterBuffer
from ...plan import ProtocolPlan
from ...steps import ProtocolStep
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from ..yk_common import SEQUENCE_MODULUS, pack_yk_frame


class AstraP1Command(IntEnum):
    RASTER = 0x00
    FEED_FORWARD = 0x02
    FEED_SPECIAL = 0x03
    FEED_BACKWARD = 0x04
    DENSITY = 0x09
    SPEED = 0x0A
    PAPER_TYPE = 0x28


class AstraP1SetupScope(str, Enum):
    FIRST_PAGE = "first_page"
    EVERY_PAGE = "every_page"


@dataclass(frozen=True)
class AstraP1Feed:
    command: AstraP1Command
    distance_dots: int
    mode: int | None = None
    last_page_mode: int | None = None

    def __post_init__(self) -> None:
        if self.command not in (
            AstraP1Command.FEED_FORWARD,
            AstraP1Command.FEED_SPECIAL,
            AstraP1Command.FEED_BACKWARD,
        ):
            raise ValueError("Astra P1 feed requires a feed command")
        if not 0 <= self.distance_dots <= 0xFFFF:
            raise ValueError("Astra P1 feed distance must fit in uint16")
        if self.command is AstraP1Command.FEED_SPECIAL and self.mode is None:
            raise ValueError("Astra P1 special feed requires a mode")
        if self.command is not AstraP1Command.FEED_SPECIAL and (
            self.mode is not None or self.last_page_mode is not None
        ):
            raise ValueError("Astra P1 feed modes apply only to special feed")

    def payload(self, *, last_page: bool = False) -> bytes:
        distance = self.distance_dots.to_bytes(2, "little")
        if self.command is not AstraP1Command.FEED_SPECIAL:
            return distance
        mode = (
            self.last_page_mode
            if last_page and self.last_page_mode is not None
            else self.mode
        )
        return bytes((int(mode),)) + distance


@dataclass(frozen=True)
class AstraP1MediaRecipe:
    setup_scope: AstraP1SetupScope
    paper_type: int | None = None
    first_feed: AstraP1Feed | None = None
    pre_raster_feed: AstraP1Feed | None = None
    page_feed: AstraP1Feed | None = None
    last_feed: AstraP1Feed | None = None


@dataclass(frozen=True)
class AstraP1VariantRecipe:
    head_width: int
    rows_per_slice: int
    speed: int
    mode_recipes: Mapping[PaperMode, AstraP1MediaRecipe]
    default_paper_mode: PaperMode
    raster_left_padding: int = 0
    stepwise: bool = False

    def __post_init__(self) -> None:
        if self.head_width <= 0:
            raise ValueError("Astra P1 head width must be greater than zero")
        if self.rows_per_slice <= 0:
            raise ValueError("Astra P1 rows per slice must be greater than zero")
        if not 0 <= self.speed <= 0xFF:
            raise ValueError("Astra P1 speed must fit in uint8")
        if not 0 <= self.raster_left_padding < self.head_width:
            raise ValueError("Astra P1 raster padding must fit inside the head")
        if self.default_paper_mode not in self.mode_recipes:
            raise ValueError("Astra P1 default paper mode requires a media recipe")


@dataclass(frozen=True)
class AstraP1FamilyRecipe:
    variants: Mapping[str, AstraP1VariantRecipe] = field(default_factory=dict)

    def supported_variants(self) -> tuple[str, ...]:
        return tuple(self.variants)

    def supported_paper_modes(self, protocol_variant: str | None = None) -> tuple[PaperMode, ...]:
        if protocol_variant is None:
            modes: list[PaperMode] = []
            for variant in self.variants.values():
                for mode in variant.mode_recipes:
                    if mode not in modes:
                        modes.append(mode)
            return tuple(modes)
        return tuple(self._variant(protocol_variant).mode_recipes)

    def build_job(self, request: PrintJobRequest) -> ProtocolPlan:
        if request.image_pipeline.encoding is not ImageEncoding.YK_ASTRA_P1_RAW:
            raise ValueError(
                "Unsupported YK Astra P1 image encoding: "
                f"{request.image_pipeline.encoding.value}"
            )
        variant = self._variant(request.protocol_variant)
        media = self._media_recipe(variant, request.paper_mode)
        raster = request.require_raster(PixelFormat.BW1)
        packets = self._build_packets(request, raster, variant, media)
        if variant.stepwise:
            return ProtocolPlan.sequence(
                tuple(
                    ProtocolStep.send("YK Astra P1 frame", packet)
                    for packet in packets
                )
            )
        return ProtocolPlan.stream(b"".join(packets))

    def _build_packets(
        self,
        request: PrintJobRequest,
        raster: RasterBuffer,
        variant: AstraP1VariantRecipe,
        media: AstraP1MediaRecipe,
    ) -> tuple[bytes, ...]:
        sequence = 0
        packets: list[bytes] = []

        def add(command: AstraP1Command, payload: bytes = b"") -> None:
            nonlocal sequence
            packets.append(pack_yk_frame(command, payload, sequence=sequence))
            sequence = (sequence + 1) % SEQUENCE_MODULUS

        configure = (
            media.setup_scope is AstraP1SetupScope.EVERY_PAGE
            or request.is_first_page
        )
        if configure:
            speed = variant.speed if request.speed is None else request.speed
            density = 9 if request.density is None else request.density
            add(AstraP1Command.SPEED, bytes((_uint8(speed, "speed"),)))
            add(AstraP1Command.DENSITY, bytes((_uint8(density, "density"),)))
            if media.paper_type is not None:
                add(
                    AstraP1Command.PAPER_TYPE,
                    bytes((0x01, _uint8(media.paper_type, "paper type"))),
                )
        if request.is_first_page and media.first_feed is not None:
            add(media.first_feed.command, media.first_feed.payload())
        if media.pre_raster_feed is not None:
            add(media.pre_raster_feed.command, media.pre_raster_feed.payload())

        data = _pack_raster(raster, variant, request.left_padding_pixels)
        row_bytes = (variant.head_width + 7) // 8
        slice_bytes = row_bytes * variant.rows_per_slice
        for offset in range(0, len(data), slice_bytes):
            add(AstraP1Command.RASTER, data[offset : offset + slice_bytes])

        if media.page_feed is not None:
            add(
                media.page_feed.command,
                media.page_feed.payload(last_page=request.is_last_page),
            )
        if request.is_last_page and media.last_feed is not None:
            add(media.last_feed.command, media.last_feed.payload(last_page=True))
        return tuple(packets)

    def _variant(self, protocol_variant: str | None) -> AstraP1VariantRecipe:
        if not protocol_variant:
            raise ValueError("YK Astra P1 requires an explicit protocol variant")
        try:
            return self.variants[protocol_variant]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported YK Astra P1 protocol variant: {protocol_variant}"
            ) from exc

    @staticmethod
    def _media_recipe(
        variant: AstraP1VariantRecipe,
        paper_mode: PaperMode | None,
    ) -> AstraP1MediaRecipe:
        mode = variant.default_paper_mode if paper_mode is None else paper_mode
        try:
            return variant.mode_recipes[mode]
        except KeyError as exc:
            raise ValueError(
                f"YK Astra P1 variant does not support paper mode: {mode.value}"
            ) from exc


def _pack_raster(
    raster: RasterBuffer,
    recipe: AstraP1VariantRecipe,
    profile_padding: int,
) -> bytes:
    raster.validate()
    padding = recipe.raster_left_padding + max(0, profile_padding)
    if padding + raster.width > recipe.head_width:
        raise ValueError(
            f"YK Astra P1 raster width {raster.width}px plus {padding}px padding "
            f"exceeds {recipe.head_width}px head"
        )
    row_bytes = (recipe.head_width + 7) // 8
    pixels = list(raster.pixels)
    packed = bytearray()
    for row in range(raster.height):
        output = bytearray(row_bytes)
        source = row * raster.width
        for column in range(raster.width):
            if pixels[source + column]:
                bit = padding + column
                output[bit >> 3] |= 0x80 >> (bit & 7)
        packed.extend(output)
    return bytes(packed)


def _uint8(value: int, label: str) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"YK Astra P1 {label} must fit in uint8")
    return value


_S001_PLAIN = AstraP1MediaRecipe(
    setup_scope=AstraP1SetupScope.FIRST_PAGE,
    paper_type=0,
    first_feed=AstraP1Feed(AstraP1Command.FEED_BACKWARD, 12),
    page_feed=AstraP1Feed(AstraP1Command.FEED_FORWARD, 52),
    last_feed=AstraP1Feed(AstraP1Command.FEED_FORWARD, 12),
)
_S001_TAG = AstraP1MediaRecipe(
    setup_scope=AstraP1SetupScope.FIRST_PAGE,
    paper_type=1,
    first_feed=AstraP1Feed(AstraP1Command.FEED_SPECIAL, 800, mode=2),
    pre_raster_feed=AstraP1Feed(AstraP1Command.FEED_FORWARD, 2),
    page_feed=AstraP1Feed(
        AstraP1Command.FEED_SPECIAL,
        800,
        mode=0,
        last_page_mode=1,
    ),
)
_S001_BLACK_TAG = AstraP1MediaRecipe(
    setup_scope=AstraP1SetupScope.FIRST_PAGE,
    paper_type=2,
    first_feed=AstraP1Feed(AstraP1Command.FEED_SPECIAL, 800, mode=2),
    pre_raster_feed=AstraP1Feed(AstraP1Command.FEED_FORWARD, 2),
    page_feed=AstraP1Feed(
        AstraP1Command.FEED_SPECIAL,
        800,
        mode=0,
        last_page_mode=1,
    ),
)

S001_VARIANT = AstraP1VariantRecipe(
    head_width=96,
    rows_per_slice=4,
    speed=25,
    default_paper_mode=PaperMode.TAG,
    raster_left_padding=6,
    mode_recipes={
        PaperMode.PLAIN: _S001_PLAIN,
        PaperMode.TAG: _S001_TAG,
        PaperMode.BLACK_TAG: _S001_BLACK_TAG,
    },
)

PUBLIC_RECIPE = AstraP1FamilyRecipe(variants={"s001": S001_VARIANT})

BEHAVIOR = ProtocolBehavior(
    requires_speed=True,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.YK_ASTRA_P1_RAW,
    ),
    image_encoding_support={
        ImageEncoding.YK_ASTRA_P1_RAW: (PixelFormat.BW1,),
    },
    supported_protocol_variants=PUBLIC_RECIPE.supported_variants(),
    supported_paper_modes=PUBLIC_RECIPE.supported_paper_modes(),
    supported_paper_modes_resolver=PUBLIC_RECIPE.supported_paper_modes,
    job_builder=PUBLIC_RECIPE.build_job,
)


__all__ = [
    "AstraP1Command",
    "AstraP1FamilyRecipe",
    "AstraP1Feed",
    "AstraP1MediaRecipe",
    "AstraP1SetupScope",
    "AstraP1VariantRecipe",
    "BEHAVIOR",
    "PUBLIC_RECIPE",
    "S001_VARIANT",
]
