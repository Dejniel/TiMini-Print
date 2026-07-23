from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Callable, Mapping, Optional, Tuple

from ...raster import PixelFormat, RasterSet
from ..family import ProtocolFamily
from ..packet import prefixed_packet_length
from ..plan import ProtocolPlan
from ..types import ImageEncoding, ImagePipelineConfig, PageFlow, PaperMode

if TYPE_CHECKING:
    from ..runtime import RuntimePrintCapabilities

ManualMotionBuilder = Callable[[int, ProtocolFamily, Optional[str]], bytes]
FamilyJobBuilder = Callable[["PrintJobRequest"], Optional[ProtocolPlan]]
PaperModeResolver = Callable[[Optional[str]], Tuple[PaperMode, ...]]


@dataclass(frozen=True)
class ProtocolBehavior:
    implemented: bool = True
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
    supported_protocol_variants: tuple[str, ...] = ()
    supported_paper_modes: tuple[PaperMode, ...] = ()
    supported_paper_modes_resolver: PaperModeResolver | None = None
    advance_paper_builder: ManualMotionBuilder | None = None
    retract_paper_builder: ManualMotionBuilder | None = None
    job_builder: FamilyJobBuilder | None = None


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
class SplitWritePlan:
    commands: tuple[bytes, ...]
    bulk_payload: bytes
    trailing_commands: tuple[bytes, ...]


@dataclass(frozen=True)
class ProtocolDefinition:
    spec: "ProtocolSpec"
    behavior: ProtocolBehavior


def split_prefixed_bulk_stream(
    data: bytes,
    protocol_family: ProtocolFamily | str,
    trailing_packets: tuple[bytes, ...] = (),
) -> SplitWritePlan:
    family = ProtocolFamily.from_value(protocol_family)
    commands = []
    trailing_commands = []
    offset = 0

    while True:
        packet_len = prefixed_packet_length(data, offset, family)
        if packet_len is None:
            break
        commands.append(data[offset : offset + packet_len])
        offset += packet_len

    if offset == len(data):
        return SplitWritePlan(tuple(commands), b"", tuple(trailing_commands))

    tail = len(data)
    for packet in trailing_packets:
        if data.endswith(packet) and tail - len(packet) >= offset:
            trailing_commands.insert(0, packet)
            tail -= len(packet)

    bulk_payload = data[offset:tail]
    if not commands and not trailing_commands:
        return SplitWritePlan((data,), b"", ())
    return SplitWritePlan(tuple(commands), bulk_payload, tuple(trailing_commands))
