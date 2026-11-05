import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.helpers import build_capture_reporter
from timiniprint.transport.bluetooth.device_information import (
    BleDeviceInformation, DEVICE_INFORMATION_CHARACTERISTICS, read_device_information,
)


def client_with_fields(**values):
    services = {}
    raw = {}
    for name, value in values.items():
        field = DEVICE_INFORMATION_CHARACTERISTICS[name]
        char = SimpleNamespace(uuid=field.uuid, properties=("read",))
        services.setdefault(field.service_uuid, []).append(char)
        raw[field.uuid] = value
    client = SimpleNamespace(
        services=[SimpleNamespace(uuid=uuid, characteristics=chars) for uuid, chars in services.items()],
        read_gatt_char=AsyncMock(side_effect=lambda char: raw[char.uuid]),
    )
    return client


class ExtendedDeviceInformationTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_serial_and_binary_pnp_are_known_optional_reads(self):
        client = client_with_fields(device_name=b"Printer", serial=b"SN-123",
                                    pnp_id=bytes.fromhex("0134127856bc9a"))
        result = await read_device_information(client, "endpoint")
        self.assertEqual(result.values, {
            "device_name": "Printer", "serial": "SN-123",
            "pnp_id": {"vendor_id_source": 1, "vendor_id": 0x1234,
                       "product_id": 0x5678, "product_version": 0x9ABC},
        })
        self.assertEqual(result.errors, {})
        self.assertTrue(result.service_present)
        self.assertEqual(client.read_gatt_char.await_count, 3)
        self.assertEqual(json.loads(json.dumps(result.as_dict()))["values"], result.values)

    async def test_gap_name_does_not_require_device_information_service(self):
        result = await read_device_information(client_with_fields(device_name=b"Printer"), "endpoint")
        self.assertFalse(result.service_present)
        self.assertEqual(result.values, {"device_name": "Printer"})

    async def test_characteristic_is_not_read_from_an_unrelated_service(self):
        client = client_with_fields(model=b"P1", pnp_id=bytes(7))
        client.services[0].uuid = "0000ff00-0000-1000-8000-00805f9b34fb"
        result = await read_device_information(client, "endpoint")
        self.assertEqual(result.values, {})
        client.read_gatt_char.assert_not_awaited()

    async def test_short_uppercase_uuids_work_without_changing_the_decoded_fields(self):
        client = client_with_fields(device_name=b"Printer")
        client.services[0].uuid = "1800"
        client.services[0].characteristics[0].uuid = "2A00"
        client.read_gatt_char = AsyncMock(return_value=b"Printer")
        result = await read_device_information(client, "endpoint")
        self.assertEqual(result.values, {"device_name": "Printer"})

    async def test_invalid_pnp_length_does_not_corrupt_other_metadata(self):
        for raw in (b"", bytes(6), bytes(8)):
            with self.subTest(length=len(raw)):
                result = await read_device_information(client_with_fields(model=b"P1", pnp_id=raw), "endpoint")
                self.assertEqual(result.values, {"model": "P1"})
                self.assertEqual(result.errors, {"pnp_id": "invalid_length"})

    async def test_timeout_stops_before_other_services_or_fields_are_read(self):
        client = client_with_fields(device_name=b"Printer", serial=b"SN-123", pnp_id=bytes(7))
        async def wait(char):
            await asyncio.sleep(1)
        client.read_gatt_char.side_effect = wait
        result = await read_device_information(client, "endpoint", timeout=.01)
        self.assertEqual(result.errors, {"device_name": "timeout"})
        self.assertEqual(client.read_gatt_char.await_count, 1)

    def test_debug_keeps_decoded_pnp_and_serial_but_not_unknown_fields_or_addresses(self):
        reporter, sink = build_capture_reporter()
        info = BleDeviceInformation("AA:BB:CC:DD:EE:01", "now", True, {
            "device_name": "Printer AA:BB:CC:DD:EE:01", "serial": "SN-123",
            "pnp_id": {"vendor_id_source": 1, "vendor_id": 42, "product_id": 7,
                       "product_version": 3, "other": "private-raster"},
        }, {})
        info.log(reporter)
        message, = sink.messages
        values = json.loads(message.detail)["values"]
        self.assertEqual(values["device_name"], "Printer <address>")
        self.assertEqual(values["serial"], "SN-123")
        self.assertEqual(values["pnp_id"], {"vendor_id_source": 1, "vendor_id": 42,
                                          "product_id": 7, "product_version": 3})
        self.assertNotIn("private-raster", repr(message))
        self.assertNotIn("AA:BB:CC:DD:EE:01", repr(message))
