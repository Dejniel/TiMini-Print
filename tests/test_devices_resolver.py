from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import reset_registry_cache
from timiniprint.devices import DeviceResolver, PrinterModelRegistry
from timiniprint.devices.resolve import ResolvedBluetoothDevice
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class DevicesResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.registry = PrinterModelRegistry.load()
        self.resolver = DeviceResolver(self.registry)

    def test_filter_printer_devices(self) -> None:
        devices = [
            DeviceInfo(name="X6H", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC),
            DeviceInfo(name="Unknown Device", address="AA:BB:CC:DD:EE:02", transport=DeviceTransport.BLE),
        ]
        out = self.resolver.filter_printer_devices(devices)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "X6H")

    def test_resolve_printer_device_selects_exact_and_contains(self) -> None:
        match = self.resolver.resolve_model_with_origin("X6H")
        logical = ResolvedBluetoothDevice(
            name="X6H-FF5F",
            model_match=match,
            classic_endpoint=DeviceInfo(name="X6H-FF5F", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC),
            ble_endpoint=None,
            display_address="AA:BB:CC:DD:EE:01",
            transport_label="[classic]",
        )
        with patch.object(self.resolver, "scan_printer_devices_with_failures", AsyncMock(return_value=([logical], []))):
            by_name = _run(self.resolver.resolve_printer_device("X6H-FF5F"))
            by_contains = _run(self.resolver.resolve_printer_device("FF5F"))
            by_address = _run(self.resolver.resolve_printer_device("AA:BB:CC:DD:EE:01"))
        self.assertEqual(by_name, logical)
        self.assertEqual(by_contains, logical)
        self.assertEqual(by_address, logical)

    def test_scan_retry_ble_when_classic_only_detected(self) -> None:
        classic = DeviceInfo(name="X6H", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H", address="UUID-1", transport=DeviceTransport.BLE)
        with patch("timiniprint.devices.resolve.SppBackend.scan_with_failures", AsyncMock(side_effect=[([classic], []), ([ble], [])])) as scan_mock:
            resolved, failures = _run(self.resolver.scan_printer_devices_with_failures(include_classic=True, include_ble=True))
        self.assertEqual(failures, [])
        self.assertEqual(scan_mock.await_count, 2)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].transport_label, "[classic+ble]")

    def test_build_connection_attempts_respects_use_spp(self) -> None:
        classic = DeviceInfo(name="X6H", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H", address="UUID-1", transport=DeviceTransport.BLE)
        spp_model = self.resolver.resolve_model_with_origin("X6H")
        ble_model = self.resolver.resolve_model_with_origin("CP01")
        d1 = ResolvedBluetoothDevice("X6H", spp_model, classic, ble, classic.address, "[classic+ble]")
        d2 = ResolvedBluetoothDevice("CP01", ble_model, classic, ble, classic.address, "[classic+ble]")
        self.assertEqual(self.resolver.build_connection_attempts(d1), [classic, ble])
        self.assertEqual(self.resolver.build_connection_attempts(d2), [ble, classic])


def _run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
