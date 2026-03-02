from __future__ import annotations

import unittest
from unittest.mock import patch

from timiniprint.transport.bluetooth.backend import _scan_blocking
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class _ScanAdapter:
    def __init__(self, devices=None, error: Exception | None = None):
        self._devices = devices or []
        self._error = error

    def scan_blocking(self, _timeout: float):
        if self._error:
            raise self._error
        return list(self._devices)


class BluetoothBackendScanTests(unittest.TestCase):
    def test_classic_and_ble_success(self) -> None:
        classic = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo("X", "UUID", transport=DeviceTransport.BLE)
        with patch("timiniprint.transport.bluetooth.backend._get_classic_adapter", return_value=_ScanAdapter([classic])), patch(
            "timiniprint.transport.bluetooth.backend._get_ble_adapter", return_value=_ScanAdapter([ble])
        ):
            devices, failures = _scan_blocking(5.0, True, True)
        self.assertEqual(len(devices), 2)
        self.assertEqual(failures, [])

    def test_only_classic(self) -> None:
        classic = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        with patch("timiniprint.transport.bluetooth.backend._get_classic_adapter", return_value=_ScanAdapter([classic])):
            devices, failures = _scan_blocking(5.0, True, False)
        self.assertEqual(devices, [classic])
        self.assertEqual(failures, [])

    def test_only_ble(self) -> None:
        ble = DeviceInfo("X", "UUID", transport=DeviceTransport.BLE)
        with patch("timiniprint.transport.bluetooth.backend._get_ble_adapter", return_value=_ScanAdapter([ble])):
            devices, failures = _scan_blocking(5.0, False, True)
        self.assertEqual(devices, [ble])
        self.assertEqual(failures, [])

    def test_both_fail_raise(self) -> None:
        with patch("timiniprint.transport.bluetooth.backend._get_classic_adapter", return_value=_ScanAdapter(error=RuntimeError("c"))), patch(
            "timiniprint.transport.bluetooth.backend._get_ble_adapter", return_value=_ScanAdapter(error=RuntimeError("b"))
        ):
            with self.assertRaisesRegex(RuntimeError, "Bluetooth scan failed"):
                _scan_blocking(5.0, True, True)


if __name__ == "__main__":
    unittest.main()
