from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Callable, Mapping, Optional, Tuple

from ...raster import PixelFormat, RasterSet
from ..family import ProtocolFamily
from ..plan import ProtocolPlan
from ..types import ImageEncoding, ImagePipelineConfig, PageFlow, PaperMode

if TYPE_CHECKING:
    from ..runtime import RuntimePrintCapabilities

ManualMotionBuilder = Callable[[int, ProtocolFamily, Optional[str]], bytes]
FamilyJobBuilder = Callable[["PrintJobRequest"], Optional[ProtocolPlan]]
PaperModeResolver = Callable[[Optional[str]], Tuple[PaperMode, ...]]
ImageEncodingSupportResolver = Callable[
    [Optional[str], Optional[PaperMode]],
    Mapping[ImageEncoding, Tuple[PixelFormat, ...]],
]


@dataclass(frozen=True)
class ProtocolBehavior:
    implemented: bool = True
    # Consumed wire controls; density/energy use the profile's blackening levels.
    print_controls: tuple[str, ...] = ()
    print_controls_resolver: Callable[[Optional[str], ImageEncoding], tuple[str, ...]] | None = None
    requires_speed: bool = False
    default_image_pipeline: ImagePipelineConfig = field(
        default_factory=lambda: ImagePipelineConfig(
            formats=(PixelFormat.BW1,),
            encoding=ImageEncoding.TINY_RAW,
        )
    )
    image_encoding_support: Mapping[ImageEncoding, tuple[PixelFormat, ...]] = field(
        default_factory=dict
    )
    image_encoding_support_resolver: ImageEncodingSupportResolver | None = None
    supported_protocol_variants: tuple[str, ...] = ()
    supported_paper_modes: tuple[PaperMode, ...] = ()
    supported_paper_modes_resolver: PaperModeResolver | None = None
    advance_paper_builder: ManualMotionBuilder | None = None
    retract_paper_builder: ManualMotionBuilder | None = None
    job_builder: FamilyJobBuilder | None = None

    def image_encoding_support_for(
        self,
        protocol_variant: str | None,
        paper_mode: PaperMode | None = None,
    ) -> Mapping[ImageEncoding, tuple[PixelFormat, ...]]:
        if self.image_encoding_support_resolver is not None:
            return self.image_encoding_support_resolver(protocol_variant, paper_mode)
        return self.image_encoding_support

    def supported_paper_modes_for(
        self,
        protocol_variant: str | None,
    ) -> tuple[PaperMode, ...]:
        if self.supported_paper_modes_resolver is not None:
            return self.supported_paper_modes_resolver(protocol_variant)
        return self.supported_paper_modes


@dataclass(frozen=True)
class PrintJobRequest:
    """Resolved raster job passed into one concrete protocol family.

    `page_index` and `page_count` let recipe code apply first-page or
    last-page marker steps without pulling pagination logic into transport.
    `page_flow` distinguishes physical pages from render chunks of one
    continuous document.
    """

    raster_set: RasterSet
    image_pipeline: ImagePipelineConfig
    is_text: bool
    speed: int | None
    energy: int
    blackening: int
    lsb_first: bool
    protocol_family: ProtocolFamily
    protocol_variant: str | None
    feed_padding: int
    dev_dpi: int
    can_print_label: bool = False
    density: int | None = None
    post_print_feed_count: int = 2
    left_padding_pixels: int = 0
    one_length: int = 0
    a4xii: bool = False
    a4_sheet_max_height: int | None = None
    image_energy: int | None = None
    back_paper_num: int | None = None
    paper_mode: PaperMode | None = None
    page_index: int = 1
    page_count: int = 1
    page_flow: PageFlow = PageFlow.PAGED
    runtime_capabilities: "RuntimePrintCapabilities | None" = None

    def require_raster(self, pixel_format: PixelFormat) -> "RasterBuffer":
        return self.raster_set.require(pixel_format)

    @property
    def default_raster(self) -> "RasterBuffer":
        return self.require_raster(self.image_pipeline.default_format)

    @property
    def width(self) -> int:
        return self.default_raster.width

    @property
    def height(self) -> int:
        return self.default_raster.height

    @property
    def is_first_page(self) -> bool:
        return self.page_index <= 1

    @property
    def is_last_page(self) -> bool:
        return self.page_index >= self.page_count

    @property
    def starts_media_page(self) -> bool:
        return self.page_flow == PageFlow.PAGED or self.is_first_page

    @property
    def ends_media_page(self) -> bool:
        return self.page_flow == PageFlow.PAGED or self.is_last_page


@dataclass(frozen=True)
class ProtocolDefinition:
    spec: "ProtocolSpec"
    behavior: ProtocolBehavior
