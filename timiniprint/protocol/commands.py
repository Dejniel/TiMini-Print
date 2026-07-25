from __future__ import annotations

from ._prefixed_commands import (
    _paper_payload,
    blackening_cmd,
    dev_state_cmd,
    energy_cmd,
    feed_paper_cmd,
    paper_cmd,
    print_mode_cmd,
)
from .families import get_protocol_behavior
from .family import ProtocolFamily
from .packet import crc8_value, make_packet


def advance_paper_cmd(
    dpi: int,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None = None,
) -> bytes:
    """Build the manual feed command packet."""
    family = ProtocolFamily.from_value(protocol_family)
    builder = get_protocol_behavior(family).advance_paper_builder
    if builder is not None:
        return builder(dpi, family, protocol_variant)
    return make_packet(0xA1, _paper_payload(dpi), family)


def retract_paper_cmd(
    dpi: int,
    protocol_family: ProtocolFamily | str,
    protocol_variant: str | None = None,
) -> bytes:
    """Build the manual retract command packet."""
    family = ProtocolFamily.from_value(protocol_family)
    builder = get_protocol_behavior(family).retract_paper_builder
    if builder is not None:
        return builder(dpi, family, protocol_variant)
    return make_packet(0xA0, _paper_payload(dpi), family)


__all__ = [
    "advance_paper_cmd",
    "blackening_cmd",
    "crc8_value",
    "dev_state_cmd",
    "energy_cmd",
    "feed_paper_cmd",
    "paper_cmd",
    "print_mode_cmd",
    "retract_paper_cmd",
]
