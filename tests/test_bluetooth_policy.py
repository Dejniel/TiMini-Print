from __future__ import annotations

import unittest

from tests.helpers import reset_registry_cache
from timiniprint.devices import BluetoothTransportPolicy, PrinterCatalog
from timiniprint.devices.bluetooth_policy import ordered_connection_endpoints, should_retry_ble_scan
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport


class BluetoothTransportPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()
        self.policy = BluetoothTransportPolicy(self.catalog)

    def test_should_retry_ble_scan_when_classic_target_has_no_ble_peer(self) -> None:
        endpoints = [
            BluetoothEndpoint(
                name="X6H-ABCD",
                address="AA:BB:CC:DD:EE:01",
                transport=BluetoothEndpointTransport.CLASSIC,
            )
        ]

        self.assertTrue(self.policy.should_retry_ble_scan(endpoints))
        self.assertTrue(should_retry_ble_scan(self.catalog, endpoints))

    def test_should_not_retry_ble_scan_when_target_already_has_ble_peer(self) -> None:
        endpoints = [
            BluetoothEndpoint(
                name="X6H-ABCD",
                address="AA:BB:CC:DD:EE:01",
                transport=BluetoothEndpointTransport.CLASSIC,
            ),
            BluetoothEndpoint(
                name="X6H-ABCD",
                address="BLE-UUID-1",
                transport=BluetoothEndpointTransport.BLE,
            ),
        ]

        self.assertFalse(self.policy.should_retry_ble_scan(endpoints))

    def test_should_retry_ble_scan_for_unsupported_classic_target(self) -> None:
        endpoints = [
            BluetoothEndpoint(
                name="Unknown Device",
                address="AA:BB:CC:DD:EE:01",
                transport=BluetoothEndpointTransport.CLASSIC,
            )
        ]

        self.assertTrue(self.policy.should_retry_ble_scan(endpoints))

    def test_ordered_connection_endpoints_prefers_classic_for_spp_profiles(self) -> None:
        device = self.policy.devices_from_endpoints(
            [
                BluetoothEndpoint(
                    name="X6H-ABCD",
                    address="AA:BB:CC:DD:EE:01",
                    transport=BluetoothEndpointTransport.CLASSIC,
                ),
                BluetoothEndpoint(
                    name="X6H-ABCD",
                    address="BLE-UUID-1",
                    transport=BluetoothEndpointTransport.BLE,
                ),
            ]
        )[0]

        endpoints = ordered_connection_endpoints(device)

        self.assertEqual(
            [endpoint.transport for endpoint in endpoints],
            [BluetoothEndpointTransport.CLASSIC, BluetoothEndpointTransport.BLE],
        )

    def test_ordered_connection_endpoints_prefers_ble_for_non_spp_profiles(self) -> None:
        device = self.policy.devices_from_endpoints(
            [
                BluetoothEndpoint(
                    name="CP01-ABCD",
                    address="AA:BB:CC:DD:EE:01",
                    transport=BluetoothEndpointTransport.CLASSIC,
                ),
                BluetoothEndpoint(
                    name="CP01-ABCD",
                    address="BLE-UUID-1",
                    transport=BluetoothEndpointTransport.BLE,
                ),
            ]
        )[0]

        endpoints = ordered_connection_endpoints(device)

        self.assertEqual(
            [endpoint.transport for endpoint in endpoints],
            [BluetoothEndpointTransport.BLE, BluetoothEndpointTransport.CLASSIC],
        )

    def test_ordered_connection_endpoints_requires_bluetooth_target(self) -> None:
        device = self.catalog.detect_device("X6H-ABCD", "AA:BB:CC:DD:EE:01")
        self.assertIsNotNone(device)

        with self.assertRaisesRegex(RuntimeError, "BluetoothTarget"):
            ordered_connection_endpoints(device)


if __name__ == "__main__":
    unittest.main()
