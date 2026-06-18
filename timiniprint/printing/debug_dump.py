from __future__ import annotations

from ..devices import PrinterDevice
from ..protocol.packet import prefixed_packet_length


def build_protocol_packet_summary(device: PrinterDevice, payload: bytes) -> dict[str, object]:
    """Return a compact packet overview for verbose diagnostics."""
    packets = build_protocol_packet_entries(device, payload)
    op_counts: dict[str, int] = {}
    for packet in packets:
        op = packet["op"]
        key = str(op) if op is not None else "raw"
        op_counts[key] = op_counts.get(key, 0) + 1
    return {
        "packet_count": len(packets),
        "op_counts": op_counts,
        "head_ops": [packet["op"] for packet in packets[:8]],
        "tail_ops": [packet["op"] for packet in packets[-8:]],
        "parse_errors": [
            packet["parse_error"]
            for packet in packets
            if "parse_error" in packet
        ],
    }


def build_protocol_packet_entries(device: PrinterDevice, payload: bytes) -> list[dict[str, object]]:
    """Return packet-level diagnostic entries for verbose/debug tools."""
    prefix = device.protocol_family.packet_prefix
    if prefix is None:
        return [
            {
                "index": 0,
                "offset": 0,
                "bytes": len(payload),
                "op": None,
                "payload_bytes": len(payload),
                "packet_head": payload[:24].hex(),
                "packet_tail": payload[-24:].hex(),
            }
        ]

    entries: list[dict[str, object]] = []
    offset = 0
    index = 0
    while offset < len(payload):
        packet_len = prefixed_packet_length(payload, offset, device.protocol_family)
        if packet_len is None:
            rest = payload[offset:]
            entries.append(
                {
                    "index": index,
                    "offset": offset,
                    "bytes": len(rest),
                    "op": None,
                    "payload_bytes": len(rest),
                    "packet_head": rest[:24].hex(),
                    "packet_tail": rest[-24:].hex(),
                    "parse_error": "not a complete prefixed packet",
                }
            )
            break
        packet = payload[offset : offset + packet_len]
        packet_payload = packet[len(prefix) + 4 : -2]
        entries.append(
            {
                "index": index,
                "offset": offset,
                "bytes": len(packet),
                "op": f"{packet[len(prefix)]:02X}",
                "payload_bytes": len(packet_payload),
                "payload_head": packet_payload[:24].hex(),
                "payload_tail": packet_payload[-24:].hex(),
                "packet_head": packet[:24].hex(),
                "packet_tail": packet[-24:].hex(),
            }
        )
        offset += packet_len
        index += 1
    return entries


__all__ = [
    "build_protocol_packet_entries",
    "build_protocol_packet_summary",
]
