from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5x import (
    V5X_FINALIZE_PACKET,
    V5X_GET_SERIAL_PACKET,
    V5X_NOTIFY_GET_SERIAL_ACK,
    V5X_NOTIFY_START_PRINT_OK,
    V5X_NOTIFY_START_READY,
    V5X_NOTIFY_TRIGGER_STATUS_POLL,
    V5X_STATUS_POLL_PACKET,
)


class _Char:
    def __init__(self, uuid: str, properties):
        self.uuid = uuid
        self.properties = properties


class _Svc:
    def __init__(self, uuid: str, chars):
        self.uuid = uuid
        self.characteristics = chars


class _Client:
    def __init__(self, services):
        self.services = services
        self.calls = []
        self.disconnected = False
        self.notify_callbacks = {}
        self.stop_notify_calls = []

    async def write_gatt_char(self, char, chunk, response=True):
        self.calls.append((char.uuid, bytes(chunk), response))

    async def start_notify(self, char_uuid, callback):
        self.notify_callbacks[char_uuid] = callback

    async def stop_notify(self, char_uuid):
        self.stop_notify_calls.append(char_uuid)
        self.notify_callbacks.pop(char_uuid, None)

    async def disconnect(self):
        self.disconnected = True

    def emit_notify(self, char_uuid: str, payload: bytes) -> None:
        callback = self.notify_callbacks[char_uuid]
        callback(char_uuid, bytearray(payload))


class BleakSocketTests(unittest.TestCase):
    def test_find_write_characteristic_preferred(self) -> None:
        s = _BleakSocket()
        services = [
            _Svc(
                "0000ae30-0000-1000-8000-00805f9b34fb",
                [_Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])],
            )
        ]
        s._client = _Client(services)
        s._connected = True
        sel = asyncio.run(s._find_write_characteristic())
        self.assertIsNotNone(sel)
        self.assertEqual(sel.strategy, "preferred_uuid")

    def test_send_async_chunks_and_response_mode(self) -> None:
        s = _BleakSocket()
        c = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        client = _Client([])
        bindings = s._transport.bindings
        s._client = client
        s._connected = True
        bindings.write_char = c
        bindings.write_selection_strategy = "preferred_uuid"
        bindings.write_response_preference = False
        bindings.write_char_uuid = c.uuid
        asyncio.run(s._send_async(b"X" * 45))
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(len(call[1]) <= 20 for call in client.calls))
        self.assertTrue(all(call[2] is False for call in client.calls))

    def test_send_async_v5x_routes_commands_and_bulk_data(self) -> None:
        s = _BleakSocket(protocol_family=ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        client = _Client([])
        bindings = s._transport.bindings
        s._client = client
        s._connected = True
        bindings.write_char = cmd
        bindings.bulk_write_char = bulk
        bindings.write_selection_strategy = "preferred_uuid"
        bindings.write_response_preference = False
        bindings.write_char_uuid = cmd.uuid
        bindings.bulk_write_char_uuid = bulk.uuid
        data = (
            V5X_GET_SERIAL_PACKET
            + bytes.fromhex("2221A20001005D94FF")
            + bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 16)
            + V5X_FINALIZE_PACKET
        )

        async def run() -> None:
            async def notify() -> None:
                while len(client.calls) < 1:
                    await asyncio.sleep(0.001)
                s._handle_notification("", bytearray(V5X_NOTIFY_GET_SERIAL_ACK))
                s._handle_notification("", bytearray(V5X_NOTIFY_START_READY))
                while len(client.calls) < 3:
                    await asyncio.sleep(0.001)
                s._handle_notification("", bytearray(V5X_NOTIFY_START_PRINT_OK))

            task = asyncio.create_task(notify())
            await s._send_async(data)
            await task

        asyncio.run(run())
        self.assertEqual(client.calls[0][0], cmd.uuid)
        self.assertEqual(client.calls[1][0], cmd.uuid)
        self.assertEqual(client.calls[2][0], cmd.uuid)
        self.assertEqual(client.calls[3][0], bulk.uuid)
        self.assertEqual(client.calls[4][0], cmd.uuid)
        self.assertEqual(client.calls[0][1], V5X_GET_SERIAL_PACKET)
        self.assertEqual(client.calls[4][1], V5X_FINALIZE_PACKET)

    def test_v5x_notify_updates_flow_state(self) -> None:
        s = _BleakSocket(protocol_family=ProtocolFamily.V5X)
        self.assertTrue(s._flow_can_write)
        s._handle_notification("", bytearray(bytes.fromhex("AA01")))
        self.assertFalse(s._flow_can_write)
        s._handle_notification("", bytearray(bytes.fromhex("AA00")))
        self.assertTrue(s._flow_can_write)

    def test_v5x_b2_notification_schedules_status_poll(self) -> None:
        s = _BleakSocket(protocol_family=ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        client = _Client([])
        bindings = s._transport.bindings
        s._client = client
        s._connected = True
        bindings.write_char = cmd
        bindings.write_selection_strategy = "preferred_uuid"
        bindings.write_response_preference = False
        bindings.write_char_uuid = cmd.uuid
        s._transport._client = client

        async def run() -> None:
            s._handle_notification("", bytearray(V5X_NOTIFY_TRIGGER_STATUS_POLL))
            await asyncio.sleep(0.75)

        asyncio.run(run())
        self.assertEqual(client.calls, [(cmd.uuid, V5X_STATUS_POLL_PACKET, False)])

    def test_find_notify_characteristic_prefers_generic_notifier(self) -> None:
        services = [
            _Svc(
                "00001800-0000-1000-8000-00805f9b34fb",
                [_Char("00002a00-0000-1000-8000-00805f9b34fb", ["read"])],
            ),
            _Svc(
                "12345678-0000-1000-8000-00805f9b34fb",
                [_Char("12345679-0000-1000-8000-00805f9b34fb", ["notify"])],
            ),
        ]
        found = _BleakSocket._find_notify_characteristic(services)
        self.assertIsNotNone(found)
        self.assertEqual(found.uuid, "12345679-0000-1000-8000-00805f9b34fb")

    def test_v5c_notify_updates_flow_state(self) -> None:
        s = _BleakSocket(protocol_family=ProtocolFamily.V5C)
        self.assertTrue(s._flow_can_write)
        s._handle_notification("", bytearray(bytes.fromhex("5688A70101000107FF")))
        self.assertFalse(s._flow_can_write)
        s._handle_notification("", bytearray(bytes.fromhex("5688A70101000000FF")))
        self.assertTrue(s._flow_can_write)

    def test_resolve_client_target_uses_cached_ble_device(self) -> None:
        cached = object()
        s = _BleakSocket(device_cache={"AA:BB:CC:DD:EE:FF": cached})
        target = asyncio.run(s._resolve_client_target("aa:bb:cc:dd:ee:ff"))
        self.assertIs(target, cached)

    def test_resolve_client_target_bypasses_cache_for_uuid_addresses(self) -> None:
        cached = object()
        address = "2123321321-2CE0-8122-7AC5-29988EF86A08"
        s = _BleakSocket(device_cache={address.upper(): cached})
        with patch("timiniprint.transport.bluetooth.adapters.bleak_adapter.IS_MACOS", True):
            target = asyncio.run(s._resolve_client_target(address.lower()))
        self.assertEqual(target, address.lower())

    def test_close_cleanup_disconnect(self) -> None:
        s = _BleakSocket()
        loop = asyncio.new_event_loop()
        s._loop = loop
        client = _Client([])
        s._client = client
        s._connected = True
        s._notify_started = True
        s._transport.bindings.notify_char_uuid = "0000ae02-0000-1000-8000-00805f9b34fb"
        s.close()
        self.assertFalse(s._connected)
        self.assertIsNone(s._client)
        self.assertIsNone(s._loop)
        self.assertEqual(client.stop_notify_calls, ["0000ae02-0000-1000-8000-00805f9b34fb"])


if __name__ == "__main__":
    unittest.main()
