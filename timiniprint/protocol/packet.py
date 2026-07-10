from __future__ import annotations

from typing import Optional

import crc8

from .family import ProtocolFamily


def crc8_value(data: bytes) -> int:
    """Return CRC8 checksum byte for the payload."""
    hasher = crc8.crc8()
    hasher.update(data)
    return hasher.digest()[0]


def make_packet(cmd: int, payload: bytes, protocol_family: ProtocolFamily | str) -> bytes:
    """Wrap a payload in the printer command packet format."""
    family = ProtocolFamily.from_value(protocol_family)
    prefix = family.require_packet_prefix()
    length = len(payload)
    header = prefix + bytes(
        [cmd & 0xFF, 0x00, length & 0xFF, (length >> 8) & 0xFF]
    )
    checksum = crc8_value(payload)
    return header + payload + bytes([checksum, 0xFF])


def prefixed_packet_length(
    data: bytes,
    offset: int,
    protocol_family: ProtocolFamily | str,
) -> Optional[int]:
    family = ProtocolFamily.from_value(protocol_family)
    prefix = family.packet_prefix
    if prefix is None:
        return None
    if offset < 0 or offset + len(prefix) + 4 > len(data):
        return None
    if data[offset : offset + len(prefix)] != prefix:
        return None
    length_offset = offset + len(prefix) + 2
    payload_length = data[length_offset] | (data[length_offset + 1] << 8)
    total_length = len(prefix) + 1 + 1 + 2 + payload_length + 2
    if offset + total_length > len(data):
        return None
    return total_length


def split_prefixed_packets(
    data: bytes,
    protocol_family: ProtocolFamily | str,
) -> list[bytes] | None:
    """Split a complete command stream into framed protocol packets."""
    packets: list[bytes] = []
    offset = 0
    while offset < len(data):
        packet_length = prefixed_packet_length(data, offset, protocol_family)
        if packet_length is None:
            return None
        packets.append(data[offset : offset + packet_length])
        offset += packet_length
    return packets


def prefixed_packet_opcode(
    packet: bytes,
    protocol_family: ProtocolFamily | str,
) -> int | None:
    family = ProtocolFamily.from_value(protocol_family)
    prefix = family.packet_prefix
    if prefix is None:
        return None
    if len(packet) < len(prefix) + 1 or packet[: len(prefix)] != prefix:
        return None
    return packet[len(prefix)]


def prefixed_packet_payload(
    packet: bytes,
    protocol_family: ProtocolFamily | str,
) -> bytes | None:
    family = ProtocolFamily.from_value(protocol_family)
    prefix = family.packet_prefix
    if prefix is None:
        return None
    packet_length = prefixed_packet_length(packet, 0, family)
    if packet_length is None:
        return None
    payload_length_offset = len(prefix) + 2
    payload_length = (
        packet[payload_length_offset]
        | (packet[payload_length_offset + 1] << 8)
    )
    payload_start = len(prefix) + 4
    return packet[payload_start : payload_start + payload_length]
