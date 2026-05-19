from __future__ import annotations

from typing import Mapping

from ..devices import PrinterDevice
from ..protocol import ProtocolJob
from ..protocol.families import get_protocol_behavior
from ..protocol.packet import prefixed_packet_length


def build_protocol_job_debug_dump(
    device: PrinterDevice,
    job: ProtocolJob,
    *,
    settings: Mapping[str, object],
) -> dict[str, object]:
    runtime_density_profile = device.runtime_density_profile
    transport = get_protocol_behavior(device.protocol_family).transport
    return {
        "schema": "timiniprint/debug-protocol-job/v1",
        "diagnostic_only": True,
        "device": {
            "display_name": device.display_name,
            "profile_key": device.profile_key,
            "protocol_family": device.protocol_family.value,
            "protocol_variant": device.protocol_variant,
            "image_pipeline": {
                "encoding": device.image_pipeline.encoding.value,
                "formats": [pixel_format.value for pixel_format in device.image_pipeline.formats],
            },
            "runtime_variant": device.runtime_variant,
            "runtime_density_profile_key": (
                None
                if runtime_density_profile is None
                else runtime_density_profile.profile_key
            ),
            "detection_rule_key": device.detection_rule_key,
        },
        "settings": dict(settings),
        "transport": {
            "connect_packets": [packet.hex() for packet in transport.connect_packets],
            "connect_delay_ms": transport.connect_delay_ms,
            "standard_chunk_cap": transport.standard_chunk_cap,
            "standard_write_delay_ms": transport.standard_write_delay_ms,
            "write_without_response_payload_reserve": (
                transport.write_without_response_payload_reserve
            ),
        },
        "job": {
            "payload_bytes": len(job.payload),
            "payload_segments": len(job.payload_segments),
            "steps": [
                {
                    "label": step.label,
                    "operation": step.operation.value,
                    "bytes": len(step.data),
                    "expect": step.expect.value,
                }
                for step in job.steps
            ],
            "runtime_controller": (
                None
                if job.runtime_controller is None
                else type(job.runtime_controller).__name__
            ),
        },
        "packets": _packet_debug_entries(device, job.payload),
        "payload_hex": job.payload.hex(),
    }


def _packet_debug_entries(device: PrinterDevice, payload: bytes) -> list[dict[str, object]]:
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
