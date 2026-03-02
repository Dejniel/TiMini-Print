from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from timiniprint.devices import DeviceResolver, PrinterModelRegistry
from timiniprint.devices.resolve import ResolvedBluetoothDevice
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class DeviceResolverLogicalDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = PrinterModelRegistry.load()
        self.resolver = DeviceResolver(self.registry)

    def test_unique_classic_and_ble_are_merged_into_one_logical_device(self) -> None:
        devices = [
            DeviceInfo(
                name="X6H",
                address="AA:BB:CC:DD:EE:01",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            DeviceInfo(
                name="X6H",
                address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
                paired=None,
                transport=DeviceTransport.BLE,
            ),
        ]
        resolved = self.resolver.build_resolved_bluetooth_devices(devices)
        self.assertEqual(len(resolved), 1)
        item = resolved[0]
        self.assertIsNotNone(item.classic_endpoint)
        self.assertIsNotNone(item.ble_endpoint)
        self.assertEqual(item.transport_label, "[classic+ble]")
        self.assertEqual(item.model.model_no, "X6H")

    def test_ambiguous_group_is_not_merged(self) -> None:
        devices = [
            DeviceInfo(
                name="X6H",
                address="AA:BB:CC:DD:EE:01",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            DeviceInfo(
                name="X6H",
                address="AA:BB:CC:DD:EE:02",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            DeviceInfo(
                name="X6H",
                address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
                paired=None,
                transport=DeviceTransport.BLE,
            ),
        ]
        resolved = self.resolver.build_resolved_bluetooth_devices(devices)
        self.assertEqual(len(resolved), 3)
        self.assertTrue(all(item.classic_endpoint is None or item.ble_endpoint is None for item in resolved))

    def test_resolve_by_classic_or_ble_address_returns_same_logical_device(self) -> None:
        logical = ResolvedBluetoothDevice(
            name="X6H",
            model_match=self.resolver.resolve_model_with_origin("X6H"),
            classic_endpoint=DeviceInfo(
                name="X6H",
                address="AA:BB:CC:DD:EE:01",
                paired=True,
                transport=DeviceTransport.CLASSIC,
            ),
            ble_endpoint=DeviceInfo(
                name="X6H",
                address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
                paired=None,
                transport=DeviceTransport.BLE,
            ),
            display_address="AA:BB:CC:DD:EE:01",
            transport_label="[classic+ble]",
        )
        with patch.object(
            self.resolver,
            "scan_printer_devices_with_failures",
            AsyncMock(return_value=([logical], [])),
        ):
            by_classic = _run(self.resolver.resolve_printer_device("AA:BB:CC:DD:EE:01"))
            by_ble = _run(self.resolver.resolve_printer_device("F4B3C8E3-C284-9C3A-C549-D786345CB553"))
        self.assertEqual(by_classic, logical)
        self.assertEqual(by_ble, logical)

    def test_connection_attempt_order_follows_model_use_spp(self) -> None:
        x6h_match = self.resolver.resolve_model_with_origin("X6H")
        cp01_match = self.resolver.resolve_model_with_origin("CP01")
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
        spp_first = ResolvedBluetoothDevice(
            name="X6H",
            model_match=x6h_match,
            classic_endpoint=classic,
            ble_endpoint=ble,
            display_address=classic.address,
            transport_label="[classic+ble]",
        )
        ble_first = ResolvedBluetoothDevice(
            name="CP01",
            model_match=cp01_match,
            classic_endpoint=classic,
            ble_endpoint=ble,
            display_address=classic.address,
            transport_label="[classic+ble]",
        )
        self.assertEqual(
            self.resolver.build_connection_attempts(spp_first),
            [classic, ble],
        )
        self.assertEqual(
            self.resolver.build_connection_attempts(ble_first),
            [ble, classic],
        )

    def test_single_endpoint_builds_single_attempt(self) -> None:
        match = self.resolver.resolve_model_with_origin("X6H")
        classic = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:01",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        resolved = ResolvedBluetoothDevice(
            name="X6H",
            model_match=match,
            classic_endpoint=classic,
            ble_endpoint=None,
            display_address=classic.address,
            transport_label="[classic]",
        )
        self.assertEqual(self.resolver.build_connection_attempts(resolved), [classic])

    def test_scan_retries_ble_when_classic_device_has_no_ble_pair(self) -> None:
        classic = DeviceInfo(
            name="X6H-FF5F",
            address="AA:BB:CC:DD:EE:01",
            paired=True,
            transport=DeviceTransport.CLASSIC,
        )
        ble = DeviceInfo(
            name="X6H-FF5F",
            address="F4B3C8E3-C284-9C3A-C549-D786345CB553",
            paired=None,
            transport=DeviceTransport.BLE,
        )
        with patch(
            "timiniprint.devices.resolve.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([classic], []), ([ble], [])]),
        ) as backend_scan:
            resolved, failures = _run(
                self.resolver.scan_printer_devices_with_failures(
                    include_classic=True,
                    include_ble=True,
                )
            )
        self.assertEqual(failures, [])
        self.assertEqual(backend_scan.await_count, 2)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].transport_label, "[classic+ble]")
        self.assertIsNotNone(resolved[0].classic_endpoint)
        self.assertIsNotNone(resolved[0].ble_endpoint)

    def test_scan_does_not_retry_ble_when_device_is_already_merged(self) -> None:
        classic = DeviceInfo(
            name="X6H",
            address="AA:BB:CC:DD:EE:02",
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
            "timiniprint.devices.resolve.SppBackend.scan_with_failures",
            AsyncMock(return_value=([classic, ble], [])),
        ) as backend_scan:
            resolved, failures = _run(
                self.resolver.scan_printer_devices_with_failures(
                    include_classic=True,
                    include_ble=True,
                )
            )
        self.assertEqual(failures, [])
        self.assertEqual(backend_scan.await_count, 1)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].transport_label, "[classic+ble]")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
