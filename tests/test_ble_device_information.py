import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock

from timiniprint.transport.bluetooth import (
    BleDeviceInformation, read_device_information, probe_ble_device_information,
)
from timiniprint.transport.bluetooth.device_information import (
    DEVICE_INFORMATION_SERVICE_UUID, DEVICE_INFORMATION_CHARACTERISTICS,
)

MODEL = DEVICE_INFORMATION_CHARACTERISTICS["model"]
FIRMWARE = DEVICE_INFORMATION_CHARACTERISTICS["firmware"]


def client_with_information(values):
    characteristics = [SimpleNamespace(uuid=uuid, properties=["read"]) for uuid in values]
    async def read(characteristic):
        value = values[characteristic.uuid]
        if isinstance(value, Exception):
            raise value
        return value
    return SimpleNamespace(
        services=[SimpleNamespace(uuid=DEVICE_INFORMATION_SERVICE_UUID, characteristics=characteristics)],
        read_gatt_char=AsyncMock(side_effect=read),
    )


class BleDeviceInformationTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_service_is_normal_and_never_reads(self):
        client = SimpleNamespace(services=[], read_gatt_char=AsyncMock())
        result = await read_device_information(client, "endpoint-a")
        self.assertFalse(result.service_present)
        self.assertEqual(result.values, {})
        self.assertEqual(result.errors, {})
        client.read_gatt_char.assert_not_awaited()

    async def test_partial_fields_are_decoded_and_tagged(self):
        result = await read_device_information(client_with_information({MODEL: b"P1\x00", FIRMWARE: b"1.2"}), "endpoint-a")
        self.assertEqual(result.values, {"model": "P1", "firmware": "1.2"})
        self.assertEqual(result.errors, {})
        self.assertEqual(result.as_dict()["address"], "endpoint-a")
        self.assertEqual(result.as_dict()["transport"], "ble")
        self.assertTrue(result.read_at)

    async def test_one_failure_does_not_discard_other_fields(self):
        result = await read_device_information(client_with_information({MODEL: OSError("denied"), FIRMWARE: b"1.2"}), "a")
        self.assertEqual(result.values, {"firmware": "1.2"})
        self.assertEqual(result.errors, {"model": "denied"})

    async def test_invalid_utf8_is_reported_not_guessed(self):
        result = await read_device_information(client_with_information({MODEL: b"\xff"}), "a")
        self.assertEqual(result.values, {})
        self.assertEqual(result.errors, {"model": "invalid_utf8"})

    async def test_nonreadable_characteristic_is_not_requested(self):
        client = client_with_information({MODEL: b"P1"})
        client.services[0].characteristics[0].properties = ["write"]
        result = await read_device_information(client, "a")
        self.assertEqual(result.errors, {"model": "not_readable"})
        client.read_gatt_char.assert_not_awaited()

    async def test_total_deadline_stops_requests_after_timeout(self):
        client = client_with_information({MODEL: b"P1", FIRMWARE: b"1.2"})
        async def never_replies(characteristic):
            await asyncio.Event().wait()
        client.read_gatt_char.side_effect = never_replies
        result = await read_device_information(client, "a", timeout=0.01)
        self.assertEqual(result.errors, {"model": "timeout"})
        self.assertEqual(client.read_gatt_char.await_count, 1)

    async def test_cancellation_is_not_a_successful_empty_snapshot(self):
        client = client_with_information({MODEL: b"P1"})
        client.read_gatt_char.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await read_device_information(client, "a")

    async def test_probe_needs_no_writable_characteristics_or_printer_profile(self):
        client = client_with_information({MODEL: b"P1"})
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        with patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=lambda *a, **kw: client)}):
            result = await probe_ble_device_information("a")
        self.assertEqual(result.values, {"model": "P1"})
        client.disconnect.assert_awaited_once()

    async def test_probe_closes_client_even_if_connect_fails(self):
        client = SimpleNamespace(connect=AsyncMock(side_effect=OSError("unavailable")), disconnect=AsyncMock())
        with patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=lambda *a, **kw: client)}):
            with self.assertRaises(OSError):
                await probe_ble_device_information("a")
        client.disconnect.assert_awaited_once()

    def test_decoder_excludes_unknown_characteristics_and_serial_number(self):
        result = BleDeviceInformation.from_gatt("a", service_present=True, values={
            MODEL: b"P1", "00002a25-0000-1000-8000-00805f9b34fb": b"serial",
        }, errors={})
        self.assertEqual(result.values, {"model": "P1"})


class BleConnectInformationTests(unittest.TestCase):
    def test_connect_logs_information_before_rejecting_missing_write_endpoint(self):
        from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket
        client = client_with_information({MODEL: b"P1"})
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        reporter = MagicMock()
        sock = _BleakSocket(reporter=reporter, device_cache={"A": object()})
        with patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=lambda *args, **kwargs: client)}):
            with self.assertRaisesRegex(RuntimeError, "writable GATT"):
                sock.connect(("A", 1))
        self.assertEqual(sock.device_information.values, {"model": "P1"})
        self.assertTrue(any(call.kwargs.get("short") == "BLE device information" for call in reporter.debug.call_args_list))
        client.disconnect.assert_awaited()
