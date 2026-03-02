from __future__ import annotations

import unittest

from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class BluetoothTypesTests(unittest.TestCase):
    def test_merge_validates_key(self) -> None:
        a = DeviceInfo("X", "AA", transport=DeviceTransport.CLASSIC)
        b = DeviceInfo("X", "BB", transport=DeviceTransport.CLASSIC)
        with self.assertRaises(ValueError):
            a.merge(b)

    def test_merge_name_and_paired_rules(self) -> None:
        a = DeviceInfo("A", "AA", paired=False, transport=DeviceTransport.CLASSIC)
        b = DeviceInfo("Longer", "AA", paired=True, transport=DeviceTransport.CLASSIC)
        c = a.merge(b)
        self.assertEqual(c.name, "Longer")
        self.assertTrue(c.paired)

    def test_dedupe_by_address_and_transport(self) -> None:
        d = [
            DeviceInfo("A", "AA", transport=DeviceTransport.CLASSIC),
            DeviceInfo("B", "AA", transport=DeviceTransport.CLASSIC),
            DeviceInfo("C", "AA", transport=DeviceTransport.BLE),
        ]
        out = DeviceInfo.dedupe(d)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
