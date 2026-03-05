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

    def settimeout(self, _t):
        return None

    def connect(self, target):
        self.target = target
        if self.fail:
            raise RuntimeError("connect failed")

    def close(self):
        self.closed = True


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

    def create_socket(self, _pairing_hint=None, reporter=None):
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


if __name__ == "__main__":
    unittest.main()
