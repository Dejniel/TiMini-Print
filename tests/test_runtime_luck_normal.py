from __future__ import annotations

import asyncio
import unittest

import pytest

from tests.test_luck_transactions import Connection
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.runtime.luck_normal import (
    LUCK_MODEL_QUERY_PACKET,
    LUCK_VERSION_QUERY_PACKET,
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
        self.query_timeouts: list[float] = []
        self.standard_payloads: list[bytes] = []
        self.debug_messages: list[str] = []
        self.warnings: list[tuple[str, str]] = []

    def report_debug(self, message: str) -> None:
        self.debug_messages.append(message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self.warnings.append((short, detail))

    def can_send_control_packet(self) -> bool:
        return self._can_send

    def can_query_control_packet(self) -> bool:
        return self._can_query

    def can_send_control_packet_wait_notification(self) -> bool:
        return False

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = packet, timeout
        return self._can_send

    async def query_control_packet(
        self, packet: bytes, *, timeout: float = 1.0, reply_complete=None,
    ) -> bytes | None:
        _ = timeout
        if not self._can_query:
            return None
        self.query_packets.append(bytes(packet))
        self.query_timeouts.append(timeout)
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
    def __init__(self, replies: list[bytes | None]) -> None:
        self.replies = list(replies)
        self.attached = []
        self.queries: list[bytes] = []

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0) -> None:
        _ = timeout
        self.attached.append(runtime_controller)

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = packet, timeout
        return False

    async def query_control_packet(
        self, packet: bytes, *, timeout: float = 1.0, reply_complete=None,
    ) -> bytes | None:
        _ = timeout
        self.queries.append(bytes(packet))
        if not self.replies:
            return None
        return self.replies.pop(0)


class LuckNormalRuntimeControllerTests(unittest.TestCase):
    def test_probe_trims_only_reply_edges_without_manufacturing_gray_suffix(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        for reply, gray in (
            (b"\x00 \rPPA2L_GY\n\x00", True),
            (b"PPA2L_G\x00Y", False),
            (b"PPA2L_G\xffY", False),
            (b"\x00\t\r\n ", False),
            ("PPA2L_GY\u3000".encode("gb2312"), False),
        ):
            with self.subTest(reply=reply):
                controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
                session = _Session(replies=[reply, b" \x001.\x0026\x00 "])
                prepared = asyncio.run(controller.prepare(device, session, timeout=10))
                self.assertEqual(prepared.capabilities.supports_gray, gray)
                self.assertEqual(controller.debug_snapshot()["firmware_version"], "1.\x0026")
                self.assertEqual(session.query_timeouts, [3, 3])

    def test_probe_respects_a_shorter_session_deadline(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(replies=[b"PPA2L_GY", b"1.26"])
        asyncio.run(controller.prepare(device, session, timeout=0.2))
        self.assertEqual(session.query_timeouts, [0.2, 0.2])

    def test_probe_enables_gray_for_gy_suffix(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(replies=["PPA2L_GY".encode("gb2312"), b"1.26"])

        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().device_from_profile("luck_ppa2l"), session, timeout=0.1))

        caps = prepared.capabilities
        self.assertIsNotNone(caps)
        self.assertTrue(caps.supports_gray)
        self.assertIsNone(caps.gray_level_override)
        self.assertEqual(
            session.query_packets,
            [LUCK_MODEL_QUERY_PACKET, LUCK_VERSION_QUERY_PACKET],
        )
        self.assertEqual(controller.debug_snapshot()["probed_model"], "PPA2L_GY")
        self.assertEqual(controller.debug_snapshot()["firmware_version"], "1.26")
        self.assertTrue(
            any("Luck query firmware" in message for message in session.debug_messages)
        )
        self.assertIn("Luck firmware: version=1.26", session.debug_messages)
        self.assertEqual(session.warnings, [])

    def test_probe_disables_gray_without_warning_for_non_gy_reply(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal")
        session = _Session(replies=["PPA2L".encode("gb2312"), None])

        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().device_from_profile("luck_ppa2l"), session, timeout=0.1))

        caps = prepared.capabilities
        self.assertIsNotNone(caps)
        self.assertFalse(caps.supports_gray)
        self.assertIsNone(controller.debug_snapshot()["firmware_version"])
        self.assertEqual(session.warnings, [])

    def test_probe_warns_and_degrades_when_query_is_unavailable(self) -> None:
        controller = LuckNormalRuntimeController(protocol_variant="lujiang_normal_h")
        session = _Session(can_send=True, can_query=False, reply=None)

        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().device_from_profile("luck_ppa2l"), session, timeout=0.1))

        caps = prepared.capabilities
        self.assertIsNotNone(caps)
        self.assertFalse(caps.supports_gray)
        self.assertEqual(caps.gray_level_override, 12)
        self.assertEqual(len(session.warnings), 1)
        self.assertIn("degraded mono-only mode", session.warnings[0][1])
        self.assertIn("gray printing will not work", session.warnings[0][1].lower())

    def test_prepare_connection_runtime_uses_public_probe_contract(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        connection = _ProbeConnection(["PPA2L_GY".encode("gb2312"), b"1.26"])
        reporter = _ConnectionReporter()

        runtime_context = asyncio.run(
            prepare_connection_runtime(device, connection, reporter=reporter)
        )

        self.assertIsNotNone(runtime_context.runtime_controller)
        self.assertIsNotNone(runtime_context.capabilities)
        self.assertTrue(runtime_context.capabilities.supports_gray)
        self.assertEqual(
            connection.queries,
            [LUCK_MODEL_QUERY_PACKET, LUCK_VERSION_QUERY_PACKET],
        )
        self.assertEqual(len(connection.attached), 1)
        self.assertEqual(reporter.warnings, [])


@pytest.mark.parametrize("notifications", [False, True])
@pytest.mark.parametrize("key,levels", [("luck_ppa2l", None), ("luck_ppa2lh", 12)])
def test_gray_probe_collects_full_identity_and_resets_on_reconnection(notifications, key, levels):
    device = PrinterCatalog.load().device_from_profile(key)
    connection = Connection(notifications=notifications, overrides={
        LUCK_MODEL_QUERY_PACKET: b" \x00PPA2L_GY\r\n",
        LUCK_VERSION_QUERY_PACKET: b" v1.26\x00",
    })
    prepared = asyncio.run(prepare_connection_runtime(device, connection, timeout=0.2))
    assert prepared.device is device
    assert prepared.capabilities.supports_gray
    assert prepared.capabilities.gray_level_override == levels
    controller = prepared.runtime_controller
    assert controller.debug_snapshot()["firmware_version"] == "v1.26"
    assert [event for event in connection.events if event[0] == "query"] == [
        ("query", LUCK_MODEL_QUERY_PACKET, 0.2), ("query", LUCK_VERSION_QUERY_PACKET, 0.2),
    ]
    if notifications:
        assert connection.events[0] == ("arm", "model")

    # Reusing a controller must not keep the previous identity or capabilities.
    for model in (b"PPA2L", None):
        connection = Connection(notifications=notifications, overrides={
            LUCK_MODEL_QUERY_PACKET: model, LUCK_VERSION_QUERY_PACKET: None,
        })
        reporter = _ConnectionReporter()
        prepared = asyncio.run(prepare_connection_runtime(
            device, connection, controller=controller, reporter=reporter,
        ))
        assert not prepared.capabilities.supports_gray
        assert prepared.capabilities.gray_level_override == levels
        assert controller.debug_snapshot()["firmware_version"] is None
        assert controller.debug_snapshot()["probed_model"] == ("PPA2L" if model else None)
        assert bool(reporter.warnings) == (model is None)


@pytest.mark.parametrize("notifications", [False, True])
@pytest.mark.parametrize("packet", [LUCK_MODEL_QUERY_PACKET, LUCK_VERSION_QUERY_PACKET])
@pytest.mark.parametrize("error", [TimeoutError, ConnectionError])
def test_optional_identity_timeout_does_not_hide_disconnect(notifications, packet, error):
    class FailingConnection(Connection):
        def _reply(self, current, timeout):
            if current == packet:
                raise error("query failed")
            return super()._reply(current, timeout)

    connection = FailingConnection(notifications=notifications, overrides={LUCK_MODEL_QUERY_PACKET: b"PPA2L_GY"})
    device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
    if error is ConnectionError:
        with pytest.raises(ConnectionError):
            asyncio.run(prepare_connection_runtime(device, connection))
    else:
        prepared = asyncio.run(prepare_connection_runtime(device, connection))
        assert prepared.capabilities.supports_gray == (packet == LUCK_VERSION_QUERY_PACKET)


if __name__ == "__main__":
    unittest.main()
