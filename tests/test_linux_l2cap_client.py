"""Unit tests for pure helpers in the Linux LE L2CAP transport adapter.

The transport-side behaviour requires a real Bluetooth controller and printer,
so those paths are validated manually. This module covers the deterministic
conversions and the smaller objects we rely on for endpoint resolution.
"""
from __future__ import annotations

import unittest

from timiniprint.transport.bluetooth.adapters.linux_l2cap_client import (
    _LinuxLeCharacteristic,
    _LinuxLeDescriptor,
    _LinuxLeService,
    _LinuxLeServiceCollection,
    _bdaddr_bytes_le,
    _pack_sockaddr_l2,
    _properties_from_bits,
    _resolve_value_handle,
    _uuid_short_from_bytes_le,
    _uuid_to_128bit_lower,
)


class BdaddrPackingTests(unittest.TestCase):
    def test_little_endian_bd_address(self) -> None:
        # `48:0F:57:C5:60:53` reverses to the kernel-side byte order.
        self.assertEqual(
            _bdaddr_bytes_le("48:0F:57:C5:60:53"),
            bytes.fromhex("5360c5570f48"),
        )

    def test_rejects_malformed_address(self) -> None:
        with self.assertRaises(ValueError):
            _bdaddr_bytes_le("not:a:mac")

    def test_sockaddr_l2_layout(self) -> None:
        # Encodes family(2) + psm(2) + bdaddr(6) + cid(2) + addr_type(1) + pad(1)
        packed = _pack_sockaddr_l2("48:0F:57:C5:60:53", 0, 4, 1)
        self.assertEqual(len(packed), 14)
        # AF_BLUETOOTH is 31 on Linux; the import is module-private, so just
        # check the encoded bytes against the known kernel-side layout.
        self.assertEqual(packed[2:4], b"\x00\x00")               # psm = 0
        self.assertEqual(packed[4:10], bytes.fromhex("5360c5570f48"))
        self.assertEqual(packed[10:12], b"\x04\x00")             # cid = 4 (ATT)
        self.assertEqual(packed[12], 1)                          # LE_PUBLIC
        self.assertEqual(packed[13], 0)                          # pad


class UuidConversionTests(unittest.TestCase):
    def test_16bit_uuid_expansion(self) -> None:
        self.assertEqual(
            _uuid_to_128bit_lower("ae01"),
            "0000ae01-0000-1000-8000-00805f9b34fb",
        )

    def test_passes_through_128bit_uuid_lowercase(self) -> None:
        self.assertEqual(
            _uuid_to_128bit_lower("0000AE30-0000-1000-8000-00805F9B34FB"),
            "0000ae30-0000-1000-8000-00805f9b34fb",
        )

    def test_short_uuid_from_le_bytes_16bit(self) -> None:
        # Bluetooth wire layout is little-endian.
        self.assertEqual(
            _uuid_short_from_bytes_le(b"\x01\xae"),
            "0000ae01-0000-1000-8000-00805f9b34fb",
        )

    def test_short_uuid_from_le_bytes_rejects_unexpected_length(self) -> None:
        with self.assertRaises(ValueError):
            _uuid_short_from_bytes_le(b"\x01\x02\x03")


class PropertyBitsTests(unittest.TestCase):
    def test_write_without_response_only(self) -> None:
        self.assertEqual(_properties_from_bits(0x04), ["write-without-response"])

    def test_notify_only(self) -> None:
        self.assertEqual(_properties_from_bits(0x10), ["notify"])

    def test_combined_read_write(self) -> None:
        # 0x02 read, 0x08 write
        self.assertEqual(sorted(_properties_from_bits(0x02 | 0x08)), ["read", "write"])


class ServiceCollectionTests(unittest.TestCase):
    def _build_collection(self) -> _LinuxLeServiceCollection:
        svc_ae30 = _LinuxLeService(
            uuid=_uuid_to_128bit_lower("ae30"), handle=0x0008, end_handle=0x0017
        )
        char_ae01 = _LinuxLeCharacteristic(
            uuid=_uuid_to_128bit_lower("ae01"),
            decl_handle=0x0009,
            value_handle=0x000A,
            prop_bits=0x04,
        )
        char_ae02 = _LinuxLeCharacteristic(
            uuid=_uuid_to_128bit_lower("ae02"),
            decl_handle=0x000B,
            value_handle=0x000C,
            prop_bits=0x10,
        )
        char_ae02.descriptors = [
            _LinuxLeDescriptor(
                uuid=_uuid_to_128bit_lower("2902"),
                handle=0x000D,
                value_handle=0x000C,
            )
        ]
        svc_ae30.characteristics = [char_ae01, char_ae02]
        char_ae01.service = svc_ae30
        char_ae02.service = svc_ae30
        return _LinuxLeServiceCollection([svc_ae30])

    def test_get_characteristic_by_short_uuid(self) -> None:
        col = self._build_collection()
        char = col.get_characteristic("ae01")
        self.assertIsNotNone(char)
        assert char is not None
        self.assertEqual(char.value_handle, 0x000A)
        self.assertEqual(char.handle, 0x000A)  # Bleak-compatible: handle == value handle
        self.assertEqual(char.properties, ["write-without-response"])

    def test_cccd_handle_lookup(self) -> None:
        col = self._build_collection()
        notify = col.get_characteristic("ae02")
        self.assertIsNotNone(notify)
        assert notify is not None
        self.assertEqual(notify.cccd_handle, 0x000D)

    def test_get_characteristic_missing_returns_none(self) -> None:
        col = self._build_collection()
        self.assertIsNone(col.get_characteristic("dead"))


class ResolveValueHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collection = ServiceCollectionTests()._build_collection()

    def test_resolve_from_characteristic_object(self) -> None:
        char = self.collection.get_characteristic("ae01")
        self.assertEqual(
            _resolve_value_handle(char, list(self.collection)),
            0x000A,
        )

    def test_resolve_from_uuid_string(self) -> None:
        self.assertEqual(
            _resolve_value_handle("ae01", list(self.collection)),
            0x000A,
        )

    def test_unknown_uuid_returns_none(self) -> None:
        self.assertIsNone(
            _resolve_value_handle("bead", list(self.collection))
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
