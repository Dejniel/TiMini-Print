from __future__ import annotations

import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor

from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.protocol import ProtocolJob
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

    def can_observe_replies(self) -> bool:
        return True

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


class V5CRuntimeConstructionTests(unittest.TestCase):
    def test_controller_construction_does_not_require_an_event_loop(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            controller = executor.submit(V5CRuntimeController).result(timeout=1)
        self.assertIsNone(controller.debug_snapshot()["status_code"])

    def test_completion_uses_the_current_jobs_event_loop(self) -> None:
        controller = V5CRuntimeController()
        session = _Session()

        async def print_job():
            async with controller.job_scope(session, ProtocolJob(payload=b"PRINT"), timeout=0.1):
                waiter = asyncio.create_task(controller.wait_for_completion(session, timeout=0.1))
                try:
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    self.assertFalse(waiter.done())
                    controller.handle_notification(session, make_packet(0xA1, b"\x00", ProtocolFamily.V5C))
                    await asyncio.wait_for(waiter, timeout=1)
                finally:
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

        asyncio.run(print_job())
        asyncio.run(print_job())


class V5CRuntimeControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_wait_outside_a_job_is_rejected(self) -> None:
        controller = V5CRuntimeController()
        with self.assertRaisesRegex(RuntimeError, "requires an active print job"):
            await controller.wait_for_completion(_Session(), timeout=0.1)

    async def test_cancelled_job_releases_its_completion_state(self) -> None:
        controller = V5CRuntimeController()
        session = _Session()
        job = ProtocolJob(payload=b"PRINT")
        with self.assertRaises(asyncio.CancelledError):
            async with controller.job_scope(session, job, timeout=0.1):
                raise asyncio.CancelledError()
        with self.assertRaisesRegex(RuntimeError, "requires an active print job"):
            await controller.wait_for_completion(session, timeout=0.1)
        async with controller.job_scope(session, job, timeout=0.1):
            controller.handle_notification(session, make_packet(0xA1, b"\x00", ProtocolFamily.V5C))
            await asyncio.wait_for(controller.wait_for_completion(session, timeout=0.1), timeout=1)

    async def test_sends_plan_as_continuous_stream_and_accepts_idle_status(self) -> None:
        controller = V5CRuntimeController()
        session = _Session()
        steps = (
            ProtocolStep.send("print data", b"PRINT"),
            ProtocolStep.send("query status", V5C_QUERY_STATUS_PACKET),
        )

        sent = await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertTrue(sent)
        self.assertEqual(session.payloads, [b"PRINT" + V5C_QUERY_STATUS_PACKET])

        controller.debug_update(status_code=0x80, status_name="printing")
        controller.handle_notification(
            session,
            make_packet(0xA1, bytes([0x00]), ProtocolFamily.V5C),
        )
        self.assertTrue(controller.debug_snapshot()["print_complete_seen"])

    async def test_send_failure_is_not_completion(self) -> None:
        controller = V5CRuntimeController()
        session = _Session(fail_on=b"PRINT" + V5C_QUERY_STATUS_PACKET)
        steps = (
            ProtocolStep.send("print data", b"PRINT"),
            ProtocolStep.send("query status", V5C_QUERY_STATUS_PACKET),
        )

        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertFalse(controller.debug_snapshot()["print_complete_seen"])

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
