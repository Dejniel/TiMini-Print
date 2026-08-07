from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import crc8

from .family import ProtocolFamily


@dataclass(frozen=True)
class PrefixedPacket:
    raw: bytes
    opcode: int
    flags: int
    payload: bytes


class PrefixedPacketStreamDecoder:
    """Decode fragmented or coalesced packets from a prefixed byte stream."""

    def __init__(self, protocol_family: ProtocolFamily | str) -> None:
        family = ProtocolFamily.from_value(protocol_family)
        self._prefix = family.require_packet_prefix()
        self._buffer = bytearray()

    def feed(self, data: bytes) -> tuple[PrefixedPacket, ...]:
        self._buffer.extend(data)
        packets: list[PrefixedPacket] = []
        prefix_length = len(self._prefix)
        header_length = prefix_length + 4

        while self._buffer:
            marker = self._buffer.find(self._prefix)
            if marker < 0:
                keep = self._partial_prefix_length()
                if keep:
                    del self._buffer[:-keep]
                else:
                    self._buffer.clear()
                break
            if marker:
                del self._buffer[:marker]
            if len(self._buffer) < header_length:
                break

            payload_length_offset = prefix_length + 2
            payload_length = int.from_bytes(
                self._buffer[payload_length_offset : payload_length_offset + 2],
                "little",
            )
            packet_length = header_length + payload_length + 2
            if len(self._buffer) < packet_length:
                break

            raw = bytes(self._buffer[:packet_length])
            payload = raw[header_length : header_length + payload_length]
            if raw[-1] != 0xFF or crc8_value(payload) != raw[-2]:
                del self._buffer[0]
                continue
            packets.append(
                PrefixedPacket(
                    raw=raw,
                    opcode=raw[prefix_length],
                    flags=raw[prefix_length + 1],
                    payload=payload,
                )
            )
            del self._buffer[:packet_length]
        return tuple(packets)

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)

    def _partial_prefix_length(self) -> int:
        limit = min(len(self._buffer), len(self._prefix) - 1)
        for length in range(limit, 0, -1):
            if self._buffer[-length:] == self._prefix[:length]:
                return length
        return 0


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
