from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import reset_registry_cache
from timiniprint.devices import BluetoothEndpointResolver, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport, BluetoothTarget
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.transport.bluetooth import BleakBluetoothConnector, BluetoothDiscovery, BluetoothScanResult
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class BluetoothDiscoveryAndConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()
        self.discovery = BluetoothDiscovery(self.catalog)

    def test_devices_from_scan_discards_unsupported_endpoints(self) -> None:
        devices = [
            DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC),
            DeviceInfo(name="Unknown Device", address="AA:BB:CC:DD:EE:02", transport=DeviceTransport.BLE),
        ]

        out = self.discovery.devices_from_scan(devices)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].display_name, "X6H-ABCD")

    def test_resolve_device_selects_by_name_contains_and_address(self) -> None:
        device = self.discovery.devices_from_scan(
            [
                DeviceInfo(
                    name="X6H-FF5F",
                    address="AA:BB:CC:DD:EE:01",
                    transport=DeviceTransport.CLASSIC,
                )
            ]
        )[0]
        with patch.object(
            self.discovery,
            "scan_report",
            AsyncMock(return_value=BluetoothScanResult(devices=[device], failures=[])),
        ):
            by_name = _run(self.discovery.resolve_device("X6H-FF5F"))
            by_contains = _run(self.discovery.resolve_device("FF5F"))
            by_address = _run(self.discovery.resolve_device("AA:BB:CC:DD:EE:01"))

        self.assertEqual(by_name, device)
        self.assertEqual(by_contains, device)
        self.assertEqual(by_address, device)

    def test_scan_retry_ble_when_classic_only_detected(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="UUID-1", transport=DeviceTransport.BLE)

        with patch(
            "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([classic], []), ([ble], [])]),
        ) as scan_mock:
            result = _run(self.discovery.scan_report(include_classic=True, include_ble=True))

        self.assertEqual(result.failures, [])
        self.assertEqual(scan_mock.await_count, 2)
        self.assertEqual(result.raw_endpoints, [classic, ble])
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].transport_badge, "[classic+ble]")
        self.assertEqual(result.devices[0].profile_key, "x6h")

    def test_blocking_scan_retry_ble_when_classic_only_detected(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="UUID-1", transport=DeviceTransport.BLE)

        with patch(
            "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures_blocking",
            side_effect=[([classic], []), ([ble], [])],
        ) as scan_mock:
            result = self.discovery.scan_report_blocking(include_classic=True, include_ble=True)

        self.assertEqual(result.failures, [])
        self.assertEqual(scan_mock.call_count, 2)
        self.assertEqual(result.raw_endpoints, [classic, ble])
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].transport_badge, "[classic+ble]")
        self.assertEqual(result.devices[0].profile_key, "x6h")

    def test_display_candidates_keep_classic_ble_fallback_for_ambiguous_supported_name(self) -> None:
        classic = DeviceInfo(
            name="P1",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )
        ble = DeviceInfo(
            name="P1",
            address="UUID-1",
            transport=DeviceTransport.BLE,
        )
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[classic, ble],
        )

        devices = self.discovery.devices_for_display(result)

        self.assertEqual(
            {device.model_key for device in devices},
            {"pocket_printer", "toprint_tspl_p1"},
        )
        for device in devices:
            with self.subTest(model_key=device.model_key):
                self.assertEqual(device.transport_badge, "[classic+ble]")
                self.assertIsInstance(device.transport_target, BluetoothTarget)
                self.assertIsNotNone(device.transport_target.classic_endpoint)
                self.assertIsNotNone(device.transport_target.ble_endpoint)

    def test_resolve_transport_target_allows_unsupported_name(self) -> None:
        classic = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )

        with patch(
            "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([classic], []), ([], [])]),
        ):
            resolved = _run(self.discovery.resolve_transport_target("PPA2L_3F19"))

        self.assertEqual(resolved.display_name, "PPA2L_3F19")
        self.assertEqual(resolved.transport_target.display_address, "AA:BB:CC:DD:EE:01")
        self.assertEqual(resolved.transport_target.transport_badge, "[classic]")

    def test_transport_target_from_endpoint_preserves_ble_endpoint(self) -> None:
        endpoint = DeviceInfo(
            name="MysteryPrinter",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.BLE,
        )

        target = self.discovery.transport_target_from_endpoint(endpoint)

        self.assertIsNone(target.classic_endpoint)
        self.assertIsNotNone(target.ble_endpoint)
        self.assertEqual(target.ble_endpoint.transport, BluetoothEndpointTransport.BLE)
        self.assertEqual(target.transport_badge, "[ble]")

    def test_printer_config_roundtrip_preserves_detected_bluetooth_metadata(self) -> None:
        auto = self.catalog.detect_device("MX10", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(auto)

        printer_config = self.catalog.serialize_printer_config(auto)
        manual = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(manual.profile_key, auto.profile_key)
        self.assertEqual(manual.protocol_family, auto.protocol_family)
        self.assertEqual(manual.image_pipeline, auto.image_pipeline)
        self.assertIsNotNone(manual.runtime_settings)
        self.assertIsNotNone(auto.runtime_settings)
        self.assertEqual(
            manual.runtime_settings.control_algorithm,
            auto.runtime_settings.control_algorithm,
        )
        self.assertEqual(manual.runtime_settings.preset_key, auto.runtime_settings.preset_key)
        self.assertEqual(
            manual.runtime_settings.capabilities,
            auto.runtime_settings.capabilities,
        )
        self.assertEqual(manual.transport_badge, auto.transport_badge)

    def test_printer_config_roundtrip_preserves_mac59_family_switch(self) -> None:
        auto = self.catalog.detect_device("MX10", "AA:BB:CC:DD:EE:59")
        self.assertIsNotNone(auto)

        manual = self.catalog.device_from_printer_config(
            self.catalog.serialize_printer_config(auto)
        )

        self.assertEqual(auto.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(manual.protocol_family, auto.protocol_family)
        self.assertEqual(manual.image_pipeline, auto.image_pipeline)
        self.assertIsNone(manual.runtime_settings)
        self.assertIsNone(auto.runtime_settings)

    def test_connector_prefers_classic_for_spp_profiles(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="X6H-ABCD", address="UUID-1", transport=DeviceTransport.BLE)
        device = self.discovery.devices_from_scan([classic, ble])[0]
        backend = MagicMock()
        backend.connect_attempts = AsyncMock()

        with patch("timiniprint.transport.bluetooth.connector.SppBackend", return_value=backend):
            connection = _run(BleakBluetoothConnector().connect(device))

        attempts = backend.connect_attempts.await_args.args[0]
        self.assertEqual([item.transport for item in attempts], [DeviceTransport.CLASSIC, DeviceTransport.BLE])
        self.assertEqual(connection._device, device)

    def test_connector_prefers_ble_for_non_spp_profiles(self) -> None:
        classic = DeviceInfo(name="CP01-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="CP01-ABCD", address="UUID-1", transport=DeviceTransport.BLE)
        device = self.discovery.devices_from_scan([classic, ble])[0]
        backend = MagicMock()
        backend.connect_attempts = AsyncMock()

        with patch("timiniprint.transport.bluetooth.connector.SppBackend", return_value=backend):
            _run(BleakBluetoothConnector().connect(device))

        attempts = backend.connect_attempts.await_args.args[0]
        self.assertEqual([item.transport for item in attempts], [DeviceTransport.BLE, DeviceTransport.CLASSIC])

    def test_ble_first_classic_match_can_use_single_anonymous_ble_endpoint(self) -> None:
        classic = DeviceInfo(name="MX10", address="A1:11:02:24:EF:A7", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="", address="EE:2E:02:06:67:16", transport=DeviceTransport.BLE)

        devices = self.discovery.devices_from_scan([classic, ble])

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.profile_key, "v5g_small_203")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertIsNotNone(device.transport_target.classic_endpoint)
        self.assertIsNotNone(device.transport_target.ble_endpoint)
        self.assertEqual(device.transport_badge, "[classic+ble]")

        backend = MagicMock()
        backend.connect_attempts = AsyncMock()
        with patch("timiniprint.transport.bluetooth.connector.SppBackend", return_value=backend):
            _run(BleakBluetoothConnector().connect(device))

        attempts = backend.connect_attempts.await_args.args[0]
        self.assertEqual([item.transport for item in attempts], [DeviceTransport.BLE, DeviceTransport.CLASSIC])
        self.assertEqual(attempts[0].address, "EE:2E:02:06:67:16")

    def test_anonymous_ble_is_not_attached_to_spp_preferred_profile(self) -> None:
        classic = DeviceInfo(name="X6H-ABCD", address="AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
        ble = DeviceInfo(name="", address="EE:2E:02:06:67:16", transport=DeviceTransport.BLE)

        devices = self.discovery.devices_from_scan([classic, ble])

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].profile_key, "x6h")
        self.assertEqual(devices[0].transport_badge, "[classic]")

    def test_anonymous_ble_is_not_attached_when_ambiguous(self) -> None:
        classic = DeviceInfo(name="MX10", address="A1:11:02:24:EF:A7", transport=DeviceTransport.CLASSIC)
        ble_one = DeviceInfo(name="", address="EE:2E:02:06:67:16", transport=DeviceTransport.BLE)
        ble_two = DeviceInfo(name="", address="EE:2E:02:06:67:17", transport=DeviceTransport.BLE)

        devices = self.discovery.devices_from_scan([classic, ble_one, ble_two])

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].profile_key, "v5g_small_203")
        self.assertEqual(devices[0].transport_badge, "[classic]")


class BluetoothEndpointResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.resolver = BluetoothEndpointResolver(PrinterCatalog.load())

    def test_resolves_raw_endpoints_without_transport_backend(self) -> None:
        classic = BluetoothEndpoint(
            name="X6H-ABCD",
            address="AA:BB:CC:DD:EE:01",
            transport=BluetoothEndpointTransport.CLASSIC,
        )
        ble = BluetoothEndpoint(
            name="X6H-ABCD",
            address="UUID-1",
            transport=BluetoothEndpointTransport.BLE,
        )

        devices = self.resolver.devices_from_endpoints([classic, ble])

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.transport_badge, "[classic+ble]")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertIs(device.transport_target.classic_endpoint, classic)
        self.assertIs(device.transport_target.ble_endpoint, ble)


def _run(coro):
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
