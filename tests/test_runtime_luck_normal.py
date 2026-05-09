from __future__ import annotations

import asyncio
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.runtime.luck_normal import (
    LUCK_DENSITY_PREFIX,
    LUCK_MODEL_QUERY_PACKET,
    LUCK_PAPER_TYPE_PREFIX,
    LUCK_STATUS_QUERY_PACKET,
    LuckNormalRuntimeController,
)


class _Session:
    def __init__(
        self,
        *,
        can_send: bool = True,
        can_query: bool = True,
        reply: bytes | None = None,
        replies: list[bytes | None] | None = None,
    ) -> None:
        self.notify_started = False
        self._can_send = can_send
        self._can_query = can_query
        self._reply = reply
        self._replies = list(replies) if replies is not None else None
        self.query_packets: list[bytes] = []
        self.standard_payloads: list[bytes] = []
        self.warnings: list[tuple[str, str]] = []

    def make_packet(self, opcode: int, payload: bytes) -> bytes:
        return bytes([opcode]) + payload

    def split_prefixed_packets(self, data: bytes):
        _ = data
        return None

    def extract_prefixed_opcode(self, payload: bytes):
        _ = payload
        return None

    def extract_prefixed_payload(self, packet: bytes):
        _ = packet
        return None

    def report_debug(self, message: str) -> None:
        _ = message

    def report_warning(self, *, short: str, detail: str) -> None:
        self.warnings.append((short, detail))

    def can_send_control_packet(self) -> bool:
        return self._can_send

    def can_query_control_packet(self) -> bool:
        return self._can_query

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = packet, timeout
        return self._can_send

    async def query_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bytes | None:
        _ = timeout
        if not self._can_query:
            return None
        self.query_packets.append(bytes(packet))
        if self._replies is not None:
            if not self._replies:
                return None
            return self._replies.pop(0)
        return self._reply

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))


class _ConnectionReporter:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, str]] = []

    def debug(self, *, short=None, detail=None, **_kwargs) -> None:
        _ = short, detail

    def warning(self, *, short=None, detail=None, **_kwargs) -> None:
        self.warnings.append((short or "", detail or ""))


class _ProbeConnection:
    def __init__(self, reply: bytes | None) -> None:
        self.reply = reply
        self.attached = []
        self.queries: list[bytes] = []

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0) -> None:
        _ = timeout
        self.attached.append(runtime_controller)

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = packet, timeout
        return False

    async def query_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bytes | None:
        _ = timeout
        self.queries.append(bytes(packet))
        return self.reply


class LuckNormalRuntimeControllerTests(unittest.TestCase):
    def test_probe_enables_gray_for_gy_suffix(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(reply="PPA2L_GY".encode("gb2312"))

        asyncio.run(controller.probe_capabilities(session, timeout=0.1))

        caps = controller.runtime_capabilities()
        self.assertIsNotNone(caps)
        self.assertTrue(caps.supports_gray)
        self.assertIsNone(caps.gray_level_override)
        self.assertEqual(session.query_packets, [LUCK_MODEL_QUERY_PACKET])
        self.assertEqual(controller.debug_snapshot()["probed_model"], "PPA2L_GY")
        self.assertEqual(session.warnings, [])

    def test_probe_disables_gray_without_warning_for_non_gy_reply(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(reply="PPA2L".encode("gb2312"))

        asyncio.run(controller.probe_capabilities(session, timeout=0.1))

        caps = controller.runtime_capabilities()
        self.assertIsNotNone(caps)
        self.assertFalse(caps.supports_gray)
        self.assertEqual(session.warnings, [])

    def test_probe_warns_and_degrades_when_query_is_unavailable(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal_h")
        session = _Session(can_send=True, can_query=False, reply=None)

        asyncio.run(controller.probe_capabilities(session, timeout=0.1))

        caps = controller.runtime_capabilities()
        self.assertIsNotNone(caps)
        self.assertFalse(caps.supports_gray)
        self.assertEqual(caps.gray_level_override, 12)
        self.assertEqual(len(session.warnings), 1)
        self.assertIn("degraded mono-only mode", session.warnings[0][1])
        self.assertIn("gray printing will not work", session.warnings[0][1].lower())

    def test_prepare_connection_runtime_uses_public_probe_contract(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        connection = _ProbeConnection(reply="PPA2L_GY".encode("gb2312"))
        reporter = _ConnectionReporter()

        runtime_context = asyncio.run(
            prepare_connection_runtime(device, connection, reporter=reporter)
        )

        self.assertIsNotNone(runtime_context.runtime_controller)
        self.assertIsNotNone(runtime_context.capabilities)
        self.assertTrue(runtime_context.capabilities.supports_gray)
        self.assertEqual(connection.queries, [LUCK_MODEL_QUERY_PACKET])
        self.assertEqual(len(connection.attached), 1)
        self.assertEqual(reporter.warnings, [])

    def test_send_standard_job_payload_interleaves_luck_queries(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(replies=[b"OK", b"\x00", b"OK"])
        density_packet = LUCK_DENSITY_PREFIX + bytes([1])
        before_paper = bytes([0x10, 0xFF, 0xF1, 0x03]) + bytes(12)
        paper_packet = LUCK_PAPER_TYPE_PREFIX + bytes([0x20])
        after_paper = bytes([0x1D, 0x76, 0x30, 0x00, 0x01])
        payload = density_packet + before_paper + paper_packet + after_paper

        handled = asyncio.run(
            controller.send_standard_job_payload(session, payload, timeout=0.1)
        )

        self.assertTrue(handled)
        self.assertEqual(
            session.query_packets,
            [density_packet, LUCK_STATUS_QUERY_PACKET, paper_packet],
        )
        self.assertEqual(session.standard_payloads, [before_paper, after_paper])
        self.assertEqual(session.warnings, [])

    def test_send_standard_job_payload_does_not_parse_paper_type_inside_bitmap(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(replies=[b"OK", b"\x00"])
        density_packet = LUCK_DENSITY_PREFIX + bytes([1])
        before_bitmap = bytes([0x10, 0xFF, 0xF1, 0x03]) + bytes(12)
        bitmap_with_paper_type_bytes = (
            bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00])
            + LUCK_PAPER_TYPE_PREFIX
            + bytes([0x20])
        )
        payload = density_packet + before_bitmap + bitmap_with_paper_type_bytes

        handled = asyncio.run(
            controller.send_standard_job_payload(session, payload, timeout=0.1)
        )

        self.assertTrue(handled)
        self.assertEqual(session.query_packets, [density_packet, LUCK_STATUS_QUERY_PACKET])
        self.assertEqual(session.standard_payloads, [before_bitmap + bitmap_with_paper_type_bytes])
        self.assertEqual(session.warnings, [])

    def test_send_standard_job_payload_falls_back_without_query_transport(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(can_query=False)

        handled = asyncio.run(
            controller.send_standard_job_payload(session, b"payload", timeout=0.1)
        )

        self.assertFalse(handled)
        self.assertEqual(session.standard_payloads, [])
        self.assertEqual(len(session.warnings), 1)
        self.assertIn("stream-only mode", session.warnings[0][1])


if __name__ == "__main__":
    unittest.main()
