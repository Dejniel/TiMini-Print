from __future__ import annotations

from .family import ProtocolFamily
from .packet import make_packet


def blackening_cmd(level: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed blackening command."""
    level = max(1, min(5, level))
    return make_packet(0xA4, bytes([0x30 + level]), protocol_family)


def energy_cmd(energy: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed energy command."""
    if energy <= 0:
        return b""
    return make_packet(
        0xAF,
        int(energy).to_bytes(2, "little", signed=False),
        protocol_family,
    )


def print_mode_cmd(is_text: bool, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed text/image mode command."""
    return make_packet(0xBE, bytes([1 if is_text else 0]), protocol_family)


def feed_paper_cmd(speed: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed feed command."""
    return make_packet(0xBD, bytes([speed & 0xFF]), protocol_family)


def _paper_payload(dpi: int) -> bytes:
    return bytes([0x48, 0x00]) if int(dpi) == 300 else bytes([0x30, 0x00])


def paper_cmd(dpi: int, protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed paper-position command."""
    return make_packet(0xA1, _paper_payload(dpi), protocol_family)


def dev_state_cmd(protocol_family: ProtocolFamily | str) -> bytes:
    """Build the common prefixed device-state query."""
    return make_packet(0xA3, b"\x00", protocol_family)


__all__ = [
    "blackening_cmd",
    "dev_state_cmd",
    "energy_cmd",
    "feed_paper_cmd",
    "paper_cmd",
    "print_mode_cmd",
]
