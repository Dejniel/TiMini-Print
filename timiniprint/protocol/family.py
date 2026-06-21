from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProtocolCommandSet(str, Enum):
    TINY = "tiny"
    LUCK_NORMAL = "luck_normal"
    V5G = "v5g"
    V5X = "v5x"
    V5C = "v5c"
    DCK = "dck"
    ELEPH_HPRT_ESC = "eleph_hprt_esc"
    INSTAPRINT_CORE = "instaprint_core"
    NIIMBOT = "niimbot"
    ELEPH_TSPL = "eleph_tspl"
    PHOMEMO_ESC = "phomemo_esc"


class ProtocolTransportStyle(str, Enum):
    STANDARD = "standard"
    SPLIT_BULK = "split_bulk"
    FLOW_CONTROLLED = "flow_controlled"


@dataclass(frozen=True)
class ProtocolSpec:
    packet_prefix: bytes | None
    command_set: ProtocolCommandSet
    transport_style: ProtocolTransportStyle


class ProtocolFamily(str, Enum):
    TINY = "tiny"
    TINY_PREFIXED = "tiny_prefixed"
    LUCK_NORMAL = "luck_normal"
    LUCK_NORMAL_A4 = "luck_normal_a4"
    V5G = "v5g"
    V5X = "v5x"
    V5C = "v5c"
    DCK = "dck"
    ELEPH_HPRT_ESC = "eleph_hprt_esc"
    INSTAPRINT_CORE = "instaprint_core"
    NIIMBOT = "niimbot"
    ELEPH_TSPL = "eleph_tspl"
    PHOMEMO_ESC = "phomemo_esc"

    @classmethod
    def from_value(cls, value: "ProtocolFamily | str | None") -> "ProtocolFamily":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.TINY
        return cls(str(value).strip().lower())

    @property
    def spec(self) -> ProtocolSpec:
        from .families import get_protocol_definition

        return get_protocol_definition(self).spec

    @property
    def packet_prefix(self) -> bytes | None:
        return self.spec.packet_prefix

    @property
    def uses_prefixed_packets(self) -> bool:
        return self.packet_prefix is not None

    def require_packet_prefix(self) -> bytes:
        prefix = self.packet_prefix
        if prefix is None:
            raise ValueError(f"{self.value} does not use prefixed command packets")
        return prefix

    @property
    def command_set(self) -> ProtocolCommandSet:
        return self.spec.command_set

    @property
    def transport_style(self) -> ProtocolTransportStyle:
        return self.spec.transport_style
