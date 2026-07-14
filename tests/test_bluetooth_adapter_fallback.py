from __future__ import annotations

import asyncio
import errno
import threading
import unittest
from unittest import mock

from tests.helpers import build_capture_reporter
from timiniprint.transport.bluetooth.adapters.adapter_fallback import _FallbackAdapter, _FallbackSocket
from timiniprint.transport.bluetooth.adapters.linux_att import (
    _LinuxAttAdapter,
    _LinuxAttSocket,
    _decode_uuid,
    _properties_from_mask,
)
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class _FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.connected = False
        self.closed = False
        self.timeout = None
        self.sent: list[bytes] = []

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, target) -> None:
        if self.fail:
            raise RuntimeError("connect failed")
        self.connected = True
        self.target = target

    def send_payload(self, data: bytes) -> int:
        self.sent.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class _FakeAdapter:
    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket
        self.scan_calls: list[float] = []

    def scan_blocking(self, timeout: float):
        self.scan_calls.append(timeout)
        return [DeviceInfo("MXW01", "AA:BB:CC:DD:EE:FF", transport=DeviceTransport.BLE)]

    def create_socket(self, pairing_hint=None, ble_profile=None, reporter=None, ble_mtu_request=None):
        _ = pairing_hint, ble_profile, reporter, ble_mtu_request
        return self.socket


class BluetoothAdapterFallbackTests(unittest.TestCase):
    def test_fallback_socket_prefers_primary_backend(self) -> None:
        reporter, sink = build_capture_reporter()
        primary = _FakeSocket()
        fallback = _FakeSocket()
        sock = _FallbackSocket(
            primary=primary,
            fallback=fallback,
            primary_name="linux-att",
            fallback_name="bleak",
            reporter=reporter,
        )

        sock.settimeout(7)
        sock.connect(("AA:BB:CC:DD:EE:FF", 1))
        sock.send_payload(b"abc")

        self.assertTrue(primary.connected)
        self.assertFalse(fallback.connected)
        self.assertEqual(primary.timeout, 7)
        self.assertEqual(fallback.timeout, 7)
        self.assertEqual(primary.sent, [b"abc"])
        self.assertTrue(any("linux-att" in item.detail for item in sink.messages))

    def test_fallback_socket_uses_fallback_on_primary_failure(self) -> None:
        reporter, sink = build_capture_reporter()
        primary = _FakeSocket(fail=True)
        fallback = _FakeSocket()
        sock = _FallbackSocket(
            primary=primary,
            fallback=fallback,
            primary_name="linux-att",
            fallback_name="bleak",
            reporter=reporter,
        )

        sock.connect(("AA:BB:CC:DD:EE:FF", 1))
        sock.send_payload(b"abc")

        self.assertTrue(primary.closed)
        self.assertTrue(fallback.connected)
        self.assertEqual(fallback.sent, [b"abc"])
        self.assertTrue(any("falling back to bleak" in item.detail for item in sink.messages))
        self.assertTrue(any("backend selected: bleak" in item.detail for item in sink.messages))

    def test_fallback_adapter_delegates_scan_to_fallback(self) -> None:
        primary = _FakeAdapter(_FakeSocket())
        fallback = _FakeAdapter(_FakeSocket())
        adapter = _FallbackAdapter(primary=primary, fallback=fallback)

        devices = adapter.scan_blocking(2.5)
        socket = adapter.create_socket()

        self.assertEqual(primary.scan_calls, [])
        self.assertEqual(fallback.scan_calls, [2.5])
        self.assertEqual(devices[0].name, "MXW01")
        self.assertIsInstance(socket, _FallbackSocket)

    def test_direct_att_adapter_is_discoveryless_socket_factory(self) -> None:
        adapter = _LinuxAttAdapter()

        self.assertEqual(adapter.scan_blocking(1.0), [])
        socket = adapter.create_socket()

        self.assertEqual(socket.__class__.__name__, "_LinuxAttSocket")

    def test_att_uuid_and_property_decoding(self) -> None:
        self.assertEqual(_decode_uuid(bytes.fromhex("30ae")), "0000ae30-0000-1000-8000-00805f9b34fb")
        self.assertEqual(_properties_from_mask(0x1C), ("write-without-response", "write", "notify"))

    def test_direct_att_notifications_are_dispatched_on_socket_loop(self) -> None:
        socket = _LinuxAttSocket()
        callback_thread_id = None
        caller_thread_id = None

        async def run() -> None:
            nonlocal callback_thread_id, caller_thread_id
            loop = asyncio.get_running_loop()
            socket._loop = loop
            received = asyncio.Event()

            def handle_notification(payload: bytes) -> None:
                nonlocal callback_thread_id
                callback_thread_id = threading.get_ident()
                self.assertEqual(payload, b"abc")
                received.set()

            socket._transport.handle_notification = handle_notification

            def emit_from_rx_thread() -> None:
                nonlocal caller_thread_id
                caller_thread_id = threading.get_ident()
                socket._handle_notification(1, b"abc")

            thread = threading.Thread(target=emit_from_rx_thread)
            thread.start()
            await asyncio.wait_for(received.wait(), timeout=0.5)
            thread.join(timeout=0.5)

        asyncio.run(run())

        self.assertIsNotNone(caller_thread_id)
        self.assertIsNotNone(callback_thread_id)
        self.assertNotEqual(caller_thread_id, callback_thread_id)

    def test_direct_att_bulk_payload_is_unavailable_before_connect(self) -> None:
        socket = _LinuxAttSocket()

        self.assertFalse(socket.can_send_bulk_payload())
        self.assertFalse(socket.send_bulk_payload(b"bulk", timeout=0.25))

    def test_direct_att_bulk_payload_delegates_to_transport_session(self) -> None:
        socket = _LinuxAttSocket()
        client = object()
        socket._connected = True
        socket._client = client
        socket._mtu_size = 244
        socket._loop = asyncio.new_event_loop()
        previous_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(previous_loop)
        socket._transport.can_send_bulk_payload = mock.Mock(return_value=True)
        socket._transport.send_bulk_payload = mock.AsyncMock(return_value=True)

        try:
            self.assertTrue(socket.can_send_bulk_payload())
            self.assertTrue(socket.send_bulk_payload(b"bulk", timeout=0.25))
            socket._transport.send_bulk_payload.assert_awaited_once_with(
                client,
                b"bulk",
                mtu_size=244,
                timeout=0.25,
            )
        finally:
            asyncio.set_event_loop(None)
            socket._loop.close()
            socket._loop = None
            previous_loop.close()

    def test_linux_att_connect_unpacks_address_channel_tuple(self) -> None:
        # The shared adapter-fallback wrapper iterates over RFCOMM channels and
        # passes the same (address, channel) tuple to every backend it owns,
        # including the LE-only Linux direct-ATT socket. The L2CAP/ATT path
        # has no use for the RFCOMM channel, but it must accept the tuple
        # form rather than crashing on `tuple.replace`. Without this, the
        # backend silently falls back to Bleak — which is the very path the
        # Linux direct-ATT workaround was added to avoid (#23).
        sock = _LinuxAttSocket()
        captured: dict[str, object] = {}

        def fake_open(address, address_type, *, timeout):
            captured["address"] = address
            raise OSError(errno.ENETUNREACH, "stop after capturing address")

        with mock.patch(
            "timiniprint.transport.bluetooth.adapters.linux_att._open_att_socket",
            side_effect=fake_open,
        ):
            with self.assertRaises(RuntimeError):
                sock.connect(("48:0F:57:C5:60:53", 1))

        self.assertEqual(captured["address"], "48:0F:57:C5:60:53")


if __name__ == "__main__":
    unittest.main()
