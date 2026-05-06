from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from ...raster import PixelFormat, RasterSet
from ..family import ProtocolFamily
from ..packet import prefixed_packet_length
from ..types import ImageEncoding, ImagePipelineConfig, PaperMode

ManualMotionBuilder = Callable[[int, ProtocolFamily, str | None], bytes]
FamilyJobBuilder = Callable[["PrintJobRequest"], bytes]
PaperModeResolver = Callable[[str | None], tuple[PaperMode, ...]]


@dataclass(frozen=True)
class FlowControlProfile:
    pause_packets: frozenset[bytes] = frozenset()
    resume_packets: frozenset[bytes] = frozenset()


@dataclass(frozen=True)
class BleTransportProfile:
    # Transport settings drive endpoint selection and write routing.
    split_bulk_writes: bool = False
    connect_packets: tuple[bytes, ...] = ()
    connect_delay_ms: int = 0
    standard_chunk_cap: int = 20
    standard_write_delay_ms: int = 50
    preferred_service_uuid: str = ""
    bulk_char_uuid: str = ""
    notify_char_uuid: str = ""
    prefer_generic_notify: bool = False
    flow_control: FlowControlProfile | None = None
    wait_for_flow_on_standard_write: bool = False
    bulk_chunk_cap: int = 180
    bulk_write_delay_ms: int = 10
    split_tail_packets: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class ProtocolBehavior:
    transport: BleTransportProfile = field(default_factory=BleTransportProfile)
    default_image_pipeline: ImagePipelineConfig = field(
        default_factory=lambda: ImagePipelineConfig(
            formats=(PixelFormat.BW1,),
            encoding=ImageEncoding.LEGACY_RAW,
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
    """

    raster_set: RasterSet
    image_pipeline: ImagePipelineConfig
    is_text: bool
    speed: int
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
    paper_mode: PaperMode | None = None
    page_index: int = 1
    page_count: int = 1

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
