from __future__ import annotations

from ..devices import PrinterDevice
from ..protocol.family import ProtocolFamily
from ..protocol.families.v5x import V5X_FINALIZE_PACKET
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
        return [_raw_entry(payload, index=0, offset=0)]

    entries: list[dict[str, object]] = []
    offset = 0
    index = 0
    while offset < len(payload):
        packet_len = prefixed_packet_length(payload, offset, device.protocol_family)
        if packet_len is None:
            entry = _raw_entry(payload[offset:], index=index, offset=offset)
            entry["parse_error"] = "not a complete prefixed packet"
            entries.append(entry)
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
        if device.protocol_family is ProtocolFamily.V5X and packet[len(prefix)] == 0xA9:
            # A9 switches from framed commands to an opaque raster stream.
            # Raster bytes may themselves look like valid command headers.
            bulk_end = len(payload)
            if payload.endswith(V5X_FINALIZE_PACKET):
                bulk_end -= len(V5X_FINALIZE_PACKET)
            if bulk_end > offset:
                entries.append(_raw_entry(payload[offset:bulk_end], index=index, offset=offset))
                offset = bulk_end
                index += 1
    return entries


def _raw_entry(payload: bytes, *, index: int, offset: int) -> dict[str, object]:
    return {
        "index": index,
        "offset": offset,
        "bytes": len(payload),
        "op": None,
        "payload_bytes": len(payload),
        "packet_head": payload[:24].hex(),
        "packet_tail": payload[-24:].hex(),
    }


__all__ = [
    "build_protocol_packet_entries",
    "build_protocol_packet_summary",
]
