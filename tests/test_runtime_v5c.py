from __future__ import annotations

import unittest

from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5c import V5C_QUERY_STATUS_PACKET
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.steps import ProtocolStep


class _Session:
    def __init__(self, *, can_send: bool = True, fail_on: bytes | None = None) -> None:
        self.can_send = can_send
        self.fail_on = fail_on
        self.payloads: list[bytes] = []
        self.warnings: list[tuple[str, str]] = []

    def can_send_standard_payload(self) -> bool:
        return self.can_send

    async def send_standard_payload(self, data: bytes) -> None:
        if data == self.fail_on:
            raise RuntimeError("send failed")
        self.payloads.append(bytes(data))

    @staticmethod
    def extract_prefixed_opcode(packet: bytes) -> int | None:
        prefix = ProtocolFamily.V5C.require_packet_prefix()
        if len(packet) < len(prefix) + 1 or not packet.startswith(prefix):
            return None
        return packet[len(prefix)]

    @staticmethod
    def extract_prefixed_payload(packet: bytes) -> bytes | None:
        prefix = ProtocolFamily.V5C.require_packet_prefix()
        if len(packet) < len(prefix) + 6 or not packet.startswith(prefix):
            return None
        length_offset = len(prefix) + 2
        payload_length = int.from_bytes(packet[length_offset : length_offset + 2], "little")
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

    def report_warning(self, *, short: str, detail: str) -> None:
        self.warnings.append((short, detail))


class V5CRuntimeControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_plan_as_continuous_stream_and_arms_status_query(self) -> None:
        controller = V5CRuntimeController()
        session = _Session()
        steps = (
            ProtocolStep.send("print data", b"PRINT"),
            ProtocolStep.send("query status", V5C_QUERY_STATUS_PACKET),
        )

        sent = await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertTrue(sent)
        self.assertEqual(session.payloads, [b"PRINT" + V5C_QUERY_STATUS_PACKET])
        self.assertTrue(controller.debug_snapshot()["query_status_in_flight"])

        controller.debug_update(status_code=0x80, status_name="printing")
        controller.handle_notification(
            session,
            make_packet(0xA1, bytes([0x00]), ProtocolFamily.V5C),
        )
        self.assertFalse(controller.debug_snapshot()["query_status_in_flight"])
        self.assertFalse(controller.debug_snapshot()["print_complete_seen"])

    async def test_query_send_failure_clears_armed_state(self) -> None:
        controller = V5CRuntimeController()
        session = _Session(fail_on=b"PRINT" + V5C_QUERY_STATUS_PACKET)
        steps = (
            ProtocolStep.send("print data", b"PRINT"),
            ProtocolStep.send("query status", V5C_QUERY_STATUS_PACKET),
        )

        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertFalse(controller.debug_snapshot()["query_status_in_flight"])

    async def test_declines_steps_without_standard_send(self) -> None:
        controller = V5CRuntimeController()
        session = _Session(can_send=False)

        sent = await controller.send_protocol_steps(
            session,
            (ProtocolStep.send("print data", b"PRINT"),),
            timeout=0.2,
        )

        self.assertFalse(sent)
        self.assertEqual(session.payloads, [])


if __name__ == "__main__":
    unittest.main()
