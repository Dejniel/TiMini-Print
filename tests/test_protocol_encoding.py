from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub


class ProtocolEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.encoding = importlib.import_module("timiniprint.protocol.encoding")

    def test_encode_run_splits_over_127(self) -> None:
        out = self.encoding.encode_run(1, 130)
        self.assertEqual(out, [255, 131])

    def test_rle_encode_line_cases(self) -> None:
        self.assertEqual(self.encoding.rle_encode_line([]), [])
        self.assertEqual(self.encoding.rle_encode_line([0, 0, 0]), [3])
        self.assertEqual(self.encoding.rle_encode_line([1, 1, 1]), [131])
        self.assertEqual(self.encoding.rle_encode_line([1, 1, 0, 0]), [130, 2])

    def test_pack_line_lsb_and_msb(self) -> None:
        line = [1, 0, 0, 0, 0, 0, 0, 0]
        self.assertEqual(self.encoding.pack_line(line, lsb_first=True), b"\x01")
        self.assertEqual(self.encoding.pack_line(line, lsb_first=False), b"\x80")

    def test_build_line_packets_width_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "Width must be divisible by 8"):
            self.encoding.build_line_packets([0, 1, 0], 3, 5, False, True, False, 0)

    def test_build_line_packets_rle_vs_raw_and_line_feed(self) -> None:
        rle_bytes = self.encoding.build_line_packets([1, 1, 1, 1, 1, 1, 1, 1], 8, 9, True, True, False, 1)
        raw_bytes = self.encoding.build_line_packets([1, 0, 1, 0, 1, 0, 1, 0], 8, 9, True, True, False, 1)
        self.assertIn(bytes([0xBF]), rle_bytes)
        self.assertIn(bytes([0xA2]), raw_bytes)
        self.assertGreaterEqual(rle_bytes.count(bytes([0xBD])), 1)


if __name__ == "__main__":
    unittest.main()
