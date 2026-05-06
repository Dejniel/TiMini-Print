from __future__ import annotations

from .families import get_protocol_behavior
from .family import ProtocolFamily
from .packet import crc8_value, make_packet


def blackening_cmd(level: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the blackening (density) command packet."""
    level = max(1, min(5, level))
    payload = bytes([0x30 + level])
    return make_packet(0xA4, payload, protocol_family)


def energy_cmd(energy: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the energy command packet (empty if energy <= 0)."""
    if energy <= 0:
        return b""
    payload = energy.to_bytes(2, "little", signed=False)
    return make_packet(0xAF, payload, protocol_family)


def print_mode_cmd(is_text: bool, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the print mode command packet (text vs image)."""
    payload = bytes([1 if is_text else 0])
    return make_packet(0xBE, payload, protocol_family)


def feed_paper_cmd(speed: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the feed paper command packet."""
    payload = bytes([speed & 0xFF])
    return make_packet(0xBD, payload, protocol_family)


def _paper_payload(dpi: int) -> bytes:
    if dpi == 300:
        return bytes([0x48, 0x00])
    return bytes([0x30, 0x00])


def paper_cmd(dpi: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the paper size/DPI command packet."""
    return make_packet(0xA1, _paper_payload(dpi), protocol_family)


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


def dev_state_cmd(protocol_family: ProtocolFamily | str) -> bytes:
    """Build the device state query command packet."""
    return make_packet(0xA3, bytes([0x00]), protocol_family)
