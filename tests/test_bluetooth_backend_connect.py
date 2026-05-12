from __future__ import annotations

import unittest
from unittest.mock import call, patch

from timiniprint import reporting
from timiniprint.transport.bluetooth.backend import (
    SppBackend,
    _MACOS_FALLBACK_COOLDOWN_SEC,
    _resolve_rfcomm_channels,
)
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class _Socket:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False
        self.target = None
        self.sent = []

    def settimeout(self, _t):
        return None

    def connect(self, target):
        self.target = target
        if self.fail:
            raise RuntimeError("connect failed")

    def close(self):
        self.closed = True

    def sendall(self, data):
        self.sent.append(bytes(data))


class _QuerySocket(_Socket):
    def __init__(self, replies):
        super().__init__(fail=False)
        self._replies = [bytes(reply) for reply in replies]
        self._timeout = None

    def gettimeout(self):
        return self._timeout

    def settimeout(self, timeout):
        self._timeout = timeout

    def recv(self, _size):
        if self._replies:
            return self._replies.pop(0)
        raise TimeoutError()


class _Reporter:
    def __init__(self):
        self.debugs = []

    def debug(self, *, short=None, detail=None, **_kwargs):
        self.debugs.append((short, detail))


class _Adapter:
    def __init__(self, channels, fail=False, pair_error=None):
        self._channels = channels
        self._fail = fail
        self._pair_error = pair_error

    def resolve_rfcomm_channels(self, _address):
        return self._channels

    def ensure_paired(self, _address, _pairing_hint=None):
        if self._pair_error:
            raise self._pair_error

    def create_socket(self, _pairing_hint=None, protocol_family=None, reporter=None):
        _ = protocol_family
        _ = reporter
        return _Socket(fail=self._fail)


class _BleScanAdapter:
    def __init__(self, devices):
        self._devices = list(devices)

    def scan_blocking(self, _timeout: float):
        return list(self._devices)


class BluetoothBackendConnectTests(unittest.TestCase):
    def test_resolve_rfcomm_channels_uses_explicit_then_fallback(self) -> None:
        adapter = _Adapter([7, "x", 3, 7])
        self.assertEqual(_resolve_rfcomm_channels(adapter, "AA"), [7, 3])
        empty = _Adapter([])
        self.assertEqual(_resolve_rfcomm_channels(empty, "AA"), [1])

    def test_connect_attempts_success_first(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        dev = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=_Adapter([1], fail=False)):
            backend._connect_attempts_blocking([dev], pairing_hint=False)
        self.assertTrue(backend.is_connected())

    def test_connect_attempts_fallback_and_final_error(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        d1 = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        d2 = DeviceInfo("X", "UUID", transport=DeviceTransport.BLE)

        def adapter_for(t):
            return _Adapter([1], fail=True if t == DeviceTransport.CLASSIC else False)

        with patch("timiniprint.transport.bluetooth.backend._select_adapter", side_effect=adapter_for):
            backend._connect_attempts_blocking([d1, d2], pairing_hint=False)
        self.assertEqual(getattr(backend, "_transport"), DeviceTransport.BLE)

        backend2 = SppBackend(reporter=reporting.DUMMY_REPORTER)
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=_Adapter([1], fail=True)):
            with self.assertRaisesRegex(RuntimeError, "connect failed"):
                backend2._connect_attempts_blocking([d1], pairing_hint=False)

    def test_macos_fallback_applies_cooldown(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        d1 = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        d2 = DeviceInfo("X", "UUID", transport=DeviceTransport.BLE)

        def adapter_for(t):
            return _Adapter([1], fail=True if t == DeviceTransport.CLASSIC else False)

        with patch("timiniprint.transport.bluetooth.backend.IS_MACOS", True), patch(
            "timiniprint.transport.bluetooth.backend._select_adapter",
            side_effect=adapter_for,
        ), patch(
            "timiniprint.transport.bluetooth.backend._get_ble_adapter",
            return_value=_BleScanAdapter([d2]),
        ), patch("timiniprint.transport.bluetooth.backend.time.sleep") as sleep_mock:
            backend._connect_attempts_blocking([d1, d2], pairing_hint=False)

        self.assertIn(call(_MACOS_FALLBACK_COOLDOWN_SEC), sleep_mock.mock_calls)

    def test_macos_ble_refresh_updates_fallback_address(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        d1 = DeviceInfo("X6H", "AA:BB:CC:DD:EE:FF", transport=DeviceTransport.CLASSIC)
        stale_ble = DeviceInfo("X6H", "OLD-UUID", transport=DeviceTransport.BLE)
        refreshed_ble = DeviceInfo("X6H", "NEW-UUID", transport=DeviceTransport.BLE)

        def adapter_for(t):
            return _Adapter([1], fail=True if t == DeviceTransport.CLASSIC else False)

        with patch("timiniprint.transport.bluetooth.backend.IS_MACOS", True), patch(
            "timiniprint.transport.bluetooth.backend._select_adapter",
            side_effect=adapter_for,
        ), patch(
            "timiniprint.transport.bluetooth.backend._get_ble_adapter",
            return_value=_BleScanAdapter([refreshed_ble]),
        ):
            backend._connect_attempts_blocking([d1, stale_ble], pairing_hint=False)

        self.assertEqual(getattr(backend, "_transport"), DeviceTransport.BLE)
        self.assertEqual(getattr(backend, "_sock").target, ("NEW-UUID", 1))

    def test_write_blocking_ble_skips_outer_chunking(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        sock = _Socket()
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.BLE

        with patch("timiniprint.transport.bluetooth.backend.time.sleep") as sleep_mock:
            backend._write_blocking(b"abcdef", chunk_size=2, delay_ms=5)

        self.assertEqual(sock.sent, [b"abcdef"])
        sleep_mock.assert_not_called()

    def test_write_blocking_classic_keeps_outer_chunking(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        sock = _Socket()
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC

        with patch("timiniprint.transport.bluetooth.backend.time.sleep") as sleep_mock:
            backend._write_blocking(b"abcdef", chunk_size=2, delay_ms=5)

        self.assertEqual(sock.sent, [b"ab", b"cd", b"ef"])
        self.assertGreaterEqual(sleep_mock.call_count, 1)

    def test_write_blocking_classic_reports_chunk_progress_for_large_payload(self) -> None:
        reporter = _Reporter()
        backend = SppBackend(reporter=reporter)
        sock = _Socket()
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC

        with patch("timiniprint.transport.bluetooth.backend.time.sleep"):
            backend._write_blocking(b"x" * 40, chunk_size=2, delay_ms=5)

        details = [detail for _short, detail in reporter.debugs]
        self.assertTrue(any("Classic payload send: bytes=40 chunk=2 chunks=20 delay_ms=5" in item for item in details))
        self.assertTrue(any("Classic payload progress: chunk=5/20 bytes=10/40" in item for item in details))
        self.assertTrue(any("Classic payload progress: chunk=10/20 bytes=20/40" in item for item in details))
        self.assertTrue(any("Classic payload progress: chunk=15/20 bytes=30/40" in item for item in details))
        self.assertTrue(any("Classic payload sent: bytes=40 chunks=20" in item for item in details))

    def test_query_control_packet_blocking_reads_classic_reply(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        sock = _QuerySocket([b"PPA2L_GY"])
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC

        reply = backend._query_control_packet_blocking(b"\x10\xff\x20\xf0", 0.1)

        self.assertEqual(sock.sent, [b"\x10\xff\x20\xf0"])
        self.assertEqual(reply, b"PPA2L_GY")

    def test_query_control_packet_blocking_stops_when_reply_matches(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        sock = _QuerySocket([b"O", b"K", b"extra"])
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC

        reply = backend._query_control_packet_blocking(
            b"\x10\xff\x10\x00\x01",
            0.1,
            lambda data: data == b"OK",
        )

        self.assertEqual(reply, b"OK")
        self.assertEqual(sock._replies, [b"extra"])

    def test_query_control_packet_blocking_returns_unmatched_reply_at_timeout(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        sock = _QuerySocket([b"B", b"A", b"D"])
        backend._sock = sock
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC

        reply = backend._query_control_packet_blocking(
            b"\x10\xff\x10\x00\x01",
            0.1,
            lambda data: data == b"OK",
        )

        self.assertEqual(reply, b"BAD")

    def test_can_query_control_packet_distinguishes_classic_and_ble(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        backend._sock = _Socket()
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC
        self.assertTrue(backend.can_query_control_packet())

        backend._transport = DeviceTransport.BLE
        self.assertFalse(backend.can_query_control_packet())


if __name__ == "__main__":
    unittest.main()
