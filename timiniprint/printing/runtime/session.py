from __future__ import annotations

from typing import TYPE_CHECKING

from ... import reporting
from ...protocol.family import ProtocolFamily
from ...protocol.packet import make_packet, prefixed_packet_length

if TYPE_CHECKING:
    from ...devices import PrinterDevice


class _RuntimeProbeSession:
    """Private probe-time session adapter built on top of a live connection."""

    def __init__(self, device: PrinterDevice, connection, *, reporter: reporting.Reporter) -> None:
        self._device = device
        self._connection = connection
        self._reporter = reporter
        self.notify_started = False

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float) -> None:
        attach_runtime_controller = getattr(self._connection, "attach_runtime_controller", None)
        if not callable(attach_runtime_controller):
            return
        await attach_runtime_controller(runtime_controller, timeout=timeout)

    def make_packet(self, opcode: int, payload: bytes) -> bytes:
        return make_packet(opcode, payload, self._device.protocol_family)

    def split_prefixed_packets(self, data: bytes) -> list[bytes] | None:
        family = ProtocolFamily.from_value(self._device.protocol_family)
        if family.packet_prefix is None:
            return None
        packets: list[bytes] = []
        offset = 0
        while offset < len(data):
            packet_len = prefixed_packet_length(data, offset, family)
            if packet_len is None:
                return None
            packets.append(data[offset : offset + packet_len])
            offset += packet_len
        return packets

    def extract_prefixed_opcode(self, payload: bytes) -> int | None:
        family = ProtocolFamily.from_value(self._device.protocol_family)
        prefix = family.packet_prefix
        if prefix is None:
            return None
        if len(payload) < len(prefix) + 1 or payload[: len(prefix)] != prefix:
            return None
        return payload[len(prefix)]

    def extract_prefixed_payload(self, packet: bytes) -> bytes | None:
        family = ProtocolFamily.from_value(self._device.protocol_family)
        prefix = family.packet_prefix
        if prefix is None:
            return None
        if len(packet) < len(prefix) + 6 or packet[: len(prefix)] != prefix:
            return None
        payload_length = packet[len(prefix) + 2] | (packet[len(prefix) + 3] << 8)
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

    def report_debug(self, message: str) -> None:
        self._reporter.debug(short="Runtime", detail=message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self._reporter.warning(short=short, detail=detail)

    def can_send_control_packet(self) -> bool:
        send_control_packet = getattr(self._connection, "send_control_packet", None)
        return callable(send_control_packet)

    def can_query_control_packet(self) -> bool:
        query_control_packet = getattr(self._connection, "query_control_packet", None)
        return callable(query_control_packet)

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        send_control_packet = getattr(self._connection, "send_control_packet", None)
        if not callable(send_control_packet):
            return False
        return await send_control_packet(packet, timeout=timeout)

    async def query_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bytes | None:
        query_control_packet = getattr(self._connection, "query_control_packet", None)
        if not callable(query_control_packet):
            return None
        return await query_control_packet(packet, timeout=timeout)
