from __future__ import annotations

from ..family import ProtocolCommandSet, ProtocolFamily, ProtocolSpec, ProtocolTransportStyle
from .base import (
    BleBulkWriteProfile,
    BleTransportProfile,
    FlowControlProfile,
    PrintJobRequest,
    ProtocolBehavior,
    ProtocolDefinition,
    SplitWritePlan,
    split_prefixed_bulk_stream,
)
from .dck import BEHAVIOR as DCK_BEHAVIOR
from .eleph_hprt_esc import BEHAVIOR as ELEPH_HPRT_ESC_BEHAVIOR
from .funny_lx import BEHAVIOR as FUNNY_LX_BEHAVIOR
from .instaprint_core import BEHAVIOR as INSTAPRINT_CORE_BEHAVIOR
from .tiny import BEHAVIOR as TINY_BEHAVIOR
from .luck.normal import BEHAVIOR as LUCK_NORMAL_BEHAVIOR
from .luck.normal_a4 import BEHAVIOR as LUCK_NORMAL_A4_BEHAVIOR
from .niimbot import BEHAVIOR as NIIMBOT_BEHAVIOR
from .phomemo_esc import BEHAVIOR as PHOMEMO_ESC_BEHAVIOR
from .eleph_tspl import BEHAVIOR as ELEPH_TSPL_BEHAVIOR
from .v5g import BEHAVIOR as V5G_BEHAVIOR
from .v5c import BEHAVIOR as V5C_BEHAVIOR
from .v5x import BEHAVIOR as V5X_BEHAVIOR

_DEFINITIONS = {
    ProtocolFamily.TINY: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x51, 0x78]),
            command_set=ProtocolCommandSet.TINY,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=TINY_BEHAVIOR,
    ),
    ProtocolFamily.TINY_PREFIXED: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x12, 0x51, 0x78]),
            command_set=ProtocolCommandSet.TINY,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=TINY_BEHAVIOR,
    ),
    ProtocolFamily.LUCK_NORMAL: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.LUCK_NORMAL,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=LUCK_NORMAL_BEHAVIOR,
    ),
    ProtocolFamily.LUCK_NORMAL_A4: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.LUCK_NORMAL,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=LUCK_NORMAL_A4_BEHAVIOR,
    ),
    ProtocolFamily.V5G: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x51, 0x78]),
            command_set=ProtocolCommandSet.V5G,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=V5G_BEHAVIOR,
    ),
    ProtocolFamily.V5X: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x22, 0x21]),
            command_set=ProtocolCommandSet.V5X,
            transport_style=ProtocolTransportStyle.SPLIT_BULK,
        ),
        behavior=V5X_BEHAVIOR,
    ),
    ProtocolFamily.V5C: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x56, 0x88]),
            command_set=ProtocolCommandSet.V5C,
            transport_style=ProtocolTransportStyle.FLOW_CONTROLLED,
        ),
        behavior=V5C_BEHAVIOR,
    ),
    ProtocolFamily.DCK: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=bytes([0x55, 0xAA]),
            command_set=ProtocolCommandSet.DCK,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=DCK_BEHAVIOR,
    ),
    ProtocolFamily.ELEPH_HPRT_ESC: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.ELEPH_HPRT_ESC,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=ELEPH_HPRT_ESC_BEHAVIOR,
    ),
    ProtocolFamily.INSTAPRINT_CORE: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.INSTAPRINT_CORE,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=INSTAPRINT_CORE_BEHAVIOR,
    ),
    ProtocolFamily.NIIMBOT: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.NIIMBOT,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=NIIMBOT_BEHAVIOR,
    ),
    ProtocolFamily.ELEPH_TSPL: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.ELEPH_TSPL,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=ELEPH_TSPL_BEHAVIOR,
    ),
    ProtocolFamily.PHOMEMO_ESC: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.PHOMEMO_ESC,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=PHOMEMO_ESC_BEHAVIOR,
    ),
    ProtocolFamily.FUNNY_LX: ProtocolDefinition(
        spec=ProtocolSpec(
            packet_prefix=None,
            command_set=ProtocolCommandSet.FUNNY_LX,
            transport_style=ProtocolTransportStyle.STANDARD,
        ),
        behavior=FUNNY_LX_BEHAVIOR,
    ),
}


def get_protocol_definition(protocol_family: ProtocolFamily | str | None) -> ProtocolDefinition:
    family = ProtocolFamily.from_value(protocol_family)
    try:
        return _DEFINITIONS[family]
    except KeyError as exc:
        raise ValueError(f"Unsupported protocol family: {family}") from exc


def get_protocol_behavior(protocol_family: ProtocolFamily | str | None) -> ProtocolBehavior:
    return get_protocol_definition(protocol_family).behavior


def protocol_requires_speed(protocol_family: ProtocolFamily | str | None) -> bool:
    return get_protocol_behavior(protocol_family).requires_speed


__all__ = [
    "BleBulkWriteProfile",
    "BleTransportProfile",
    "FlowControlProfile",
    "PrintJobRequest",
    "ProtocolBehavior",
    "ProtocolDefinition",
    "SplitWritePlan",
    "get_protocol_behavior",
    "get_protocol_definition",
    "protocol_requires_speed",
    "split_prefixed_bulk_stream",
]
