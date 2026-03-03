from __future__ import annotations

import unittest
from unittest.mock import patch

from timiniprint import reporting
from timiniprint.transport.bluetooth.backend import (
    SppBackend,
    _resolve_rfcomm_channels,
    _scan_blocking,
    _unique_attempts,
)
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class _FailingSocket:
    def __init__(self, message: str) -> None:
        self._message = message

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, _target) -> None:
        raise RuntimeError(self._message)

    def close(self) -> None:
        return None


class _SuccessSocket:
    def __init__(self) -> None:
        self.target = None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, target) -> None:
        self.target = target

    def close(self) -> None:
        return None


class _ClassicAdapter:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    def ensure_paired(self, _address: str, _pairing_hint=None) -> None:
        return None

    def resolve_rfcomm_channels(self, _address: str):
        return []

    def create_socket(self, _pairing_hint=None, reporter=None):
        if self._fail:
            return _FailingSocket("classic connect failed")
        return _SuccessSocket()


class _BleAdapter:
    def __init__(self, fail: bool) -> None:
        self._fail = fail
        self.socket = _SuccessSocket()

    def ensure_paired(self, _address: str, _pairing_hint=None) -> None:
        return None

    def resolve_rfcomm_channels(self, _address: str):
        return [1]

    def create_socket(self, _pairing_hint=None, reporter=None):
        if self._fail:
            return _FailingSocket("ble connect failed")
        return self.socket


class _ScanAdapter:
    def __init__(self, devices):
        self._devices = list(devices)

    def scan_blocking(self, _timeout: float):
        return list(self._devices)


class _ExplicitChannelsAdapter:
    def resolve_rfcomm_channels(self, _address: str):
        return [7, 3, 7]


class BackendBleFallbackTests(unittest.TestCase):
    def test_connect_attempts_falls_back_to_second_transport(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        classic_device = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:FF",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        ble_device = DeviceInfo(
            name="X6H",
            address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
            paired=None,
            transport=DeviceTransport.BLE,
        )
        classic_adapter = _ClassicAdapter(fail=True)
        ble_adapter = _BleAdapter(fail=False)

        def _adapter_for(transport: DeviceTransport):
            if transport == DeviceTransport.CLASSIC:
                return classic_adapter
            return ble_adapter

        with patch(
            "timiniprint.transport.bluetooth.backend._select_adapter",
            side_effect=_adapter_for,
        ):
            backend._connect_attempts_blocking([classic_device, ble_device], pairing_hint=False)

        self.assertTrue(backend.is_connected())
        self.assertEqual(getattr(backend, "_transport"), DeviceTransport.BLE)
        self.assertEqual(getattr(backend, "_channel"), 1)

    def test_connect_attempts_without_fallback_raises_original_error(self) -> None:
        backend = SppBackend(reporter=reporting.DUMMY_REPORTER)
        classic_device = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:FF",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        classic_adapter = _ClassicAdapter(fail=True)
        with patch(
            "timiniprint.transport.bluetooth.backend._select_adapter",
            return_value=classic_adapter,
        ):
            with self.assertRaisesRegex(RuntimeError, "classic connect failed"):
                backend._connect_attempts_blocking([classic_device], pairing_hint=False)

    def test_unique_attempts_dedupes_same_transport_and_address(self) -> None:
        first = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:FF",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        second = DeviceInfo(
            name="X6H-copy",
            address="aa:bb:cc:dd:ee:ff",
            paired=False,
            transport=DeviceTransport.CLASSIC,
        )
        third = DeviceInfo(
            name="X6H-BLE",
            address="AA:BB:CC:DD:EE:FF",
            paired=None,
            transport=DeviceTransport.BLE,
        )
        unique = _unique_attempts([first, second, third])
        self.assertEqual(unique, [first, third])

    def test_scan_blocking_returns_classic_and_ble_results(self) -> None:
        classic = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:01",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        ble = DeviceInfo(
            name="X6H",
            address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
            paired=None,
            transport=DeviceTransport.BLE,
        )
        with patch(
            "timiniprint.transport.bluetooth.backend._get_classic_adapter",
            return_value=_ScanAdapter([classic]),
        ), patch(
            "timiniprint.transport.bluetooth.backend._get_ble_adapter",
            return_value=_ScanAdapter([ble]),
        ):
            devices, failures = _scan_blocking(5.0, include_classic=True, include_ble=True)
        self.assertEqual(devices, [classic, ble])
        self.assertEqual(failures, [])

    def test_resolve_rfcomm_channels_prefers_explicit_adapter_channels(self) -> None:
        adapter = _ExplicitChannelsAdapter()
        self.assertEqual(_resolve_rfcomm_channels(adapter, "AA:BB:CC:DD:EE:01"), [7, 3])


if __name__ == "__main__":
    unittest.main()
