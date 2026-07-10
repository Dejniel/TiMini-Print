from __future__ import annotations

import unittest
from unittest.mock import patch

from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5x import (
    V5X_FINALIZE_PACKET,
    V5X_GET_SERIAL_PACKET,
    V5X_NOTIFY_GET_SERIAL_ACK,
    V5X_NOTIFY_START_PRINT_OK,
    V5X_NOTIFY_START_READY,
)
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.steps import ProtocolStep


class _Session:
    def __init__(
        self,
        controller: V5XRuntimeController,
        *,
        can_send_bulk: bool = True,
    ) -> None:
        self.controller = controller
        self.can_send_bulk = can_send_bulk
        self.events: list[tuple[str, bytes]] = []
        self.debug: list[str] = []

    @staticmethod
    def make_packet(opcode: int, payload: bytes) -> bytes:
        return make_packet(opcode, payload, ProtocolFamily.V5X)

    @staticmethod
    def extract_prefixed_opcode(packet: bytes) -> int | None:
        prefix = ProtocolFamily.V5X.require_packet_prefix()
        if len(packet) < len(prefix) + 1 or not packet.startswith(prefix):
            return None
        return packet[len(prefix)]

    @staticmethod
    def extract_prefixed_payload(packet: bytes) -> bytes | None:
        prefix = ProtocolFamily.V5X.require_packet_prefix()
        if len(packet) < len(prefix) + 6 or not packet.startswith(prefix):
            return None
        length_offset = len(prefix) + 2
        payload_length = int.from_bytes(packet[length_offset : length_offset + 2], "little")
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

    @staticmethod
    def can_send_control_packet() -> bool:
        return True

    def can_send_bulk_payload(self) -> bool:
        return self.can_send_bulk

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = timeout
        self.events.append(("control", bytes(packet)))
        opcode = self.extract_prefixed_opcode(packet)
        if opcode == 0xA7:
            self.controller.handle_notification(self, V5X_NOTIFY_GET_SERIAL_ACK)
            self.controller.handle_notification(self, V5X_NOTIFY_START_READY)
        elif opcode == 0xA9:
            self.controller.handle_notification(self, V5X_NOTIFY_START_PRINT_OK)
        return True

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool:
        _ = timeout
        self.events.append(("bulk", bytes(data)))
        return True

    async def wait_for_notification(self, *args, **kwargs):
        raise AssertionError("pre-armed V5X acknowledgements should already be recorded")

    def report_debug(self, message: str) -> None:
        self.debug.append(message)

    def report_warning(self, *, short: str, detail: str) -> None:
        raise AssertionError(f"unexpected warning: {short}: {detail}")


def _page_payload() -> bytes:
    return (
        V5X_GET_SERIAL_PACKET
        + make_packet(0xA2, bytes([0x5D]), ProtocolFamily.V5X)
        + make_packet(
            0xA9,
            bytes.fromhex("010030000000"),
            ProtocolFamily.V5X,
        )
        + bytes.fromhex("AA55AA55")
        + V5X_FINALIZE_PACKET
    )


class V5XRuntimeControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_each_page_as_command_bulk_finalize_sequence(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        steps = (
            ProtocolStep.send("page 1", _page_payload()),
            ProtocolStep.send("page 2", _page_payload()),
        )

        with patch("timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0):
            sent = await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertTrue(sent)
        event_shape = [
            (kind, session.extract_prefixed_opcode(data) if kind == "control" else data)
            for kind, data in session.events
        ]
        self.assertEqual(
            event_shape,
            [
                ("control", 0xA7),
                ("control", 0xA2),
                ("control", 0xA9),
                ("bulk", bytes.fromhex("AA55AA55")),
                ("control", 0xAD),
                ("control", 0xA7),
                ("control", 0xA9),
                ("bulk", bytes.fromhex("AA55AA55")),
                ("control", 0xAD),
            ],
        )

    async def test_rejects_bulk_job_without_bulk_transport_capability(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller, can_send_bulk=False)

        with self.assertRaisesRegex(RuntimeError, "V5X bulk payload send unavailable"):
            await controller.send_protocol_steps(
                session,
                (ProtocolStep.send("print data", _page_payload()),),
                timeout=0.2,
            )

        self.assertEqual(session.events, [])


if __name__ == "__main__":
    unittest.main()
