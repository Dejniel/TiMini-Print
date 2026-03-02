from __future__ import annotations

import asyncio
import unittest

from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket


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

    async def write_gatt_char(self, char, chunk, response=True):
        self.calls.append((char.uuid, bytes(chunk), response))

    async def disconnect(self):
        self.disconnected = True


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
        s._client = client
        s._connected = True
        s._write_char = c
        s._write_selection_strategy = "preferred_uuid"
        s._write_response_preference = False
        s._write_char_uuid = c.uuid
        asyncio.run(s._send_async(b"X" * 45))
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(len(call[1]) <= 20 for call in client.calls))
        self.assertTrue(all(call[2] is False for call in client.calls))

    def test_close_cleanup_disconnect(self) -> None:
        s = _BleakSocket()
        loop = asyncio.new_event_loop()
        s._loop = loop
        s._client = _Client([])
        s._connected = True
        s.close()
        self.assertFalse(s._connected)
        self.assertIsNone(s._client)
        self.assertIsNone(s._loop)


if __name__ == "__main__":
    unittest.main()
