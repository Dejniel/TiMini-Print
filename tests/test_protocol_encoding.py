from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub


class ProtocolEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.encoding = importlib.import_module("timiniprint.protocol.encoding")
        cls.types = importlib.import_module("timiniprint.protocol.types")

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
        for width in (0, -1):
            with self.assertRaisesRegex(ValueError, "Width must be greater than zero"):
                self.encoding.build_line_packets(
                    [], width, 5, self.types.ImageEncoding.TINY_RAW, True, "tiny", 0,
                )

    def test_unaligned_rows_keep_bit_order_rle_choice_and_feed_interval(self) -> None:
        from timiniprint.protocol.family import ProtocolFamily
        from timiniprint.protocol.packet import PrefixedPacketStreamDecoder

        for family in (ProtocolFamily.TINY, ProtocolFamily.TINY_PREFIXED):
            for width in (1, 7, 9, 17, 383):
                for lsb in (False, True):
                    for encoding in (self.types.ImageEncoding.TINY_RAW, self.types.ImageEncoding.TINY_RLE):
                        with self.subTest(family=family, width=width, lsb=lsb, encoding=encoding):
                            line = [int(i % 2 == 0) for i in range(width)]
                            data = self.encoding.build_line_packets(line * 201, width, 40, encoding, lsb, family, 200)
                            packets = PrefixedPacketStreamDecoder(family).feed(data)
                            rle = self.encoding.rle_encode_line(line)
                            use_rle = encoding is self.types.ImageEncoding.TINY_RLE and len(rle) <= (width + 7) // 8
                            expected = bytes(rle) if use_rle else self.encoding.pack_line(line, lsb)
                            self.assertEqual(len(packets), 202)
                            self.assertEqual(packets[200].opcode, 0xBD)
                            self.assertEqual(packets[200].payload, b"\x28")
                            for packet in packets[:200] + packets[201:]:
                                self.assertEqual(packet.opcode, 0xBF if use_rle else 0xA2)
                                self.assertEqual(packet.payload, expected)

    def test_build_line_packets_rle_vs_raw_and_line_feed(self) -> None:
        rle_bytes = self.encoding.build_line_packets(
            [1, 1, 1, 1, 1, 1, 1, 1],
            8,
            9,
            self.types.ImageEncoding.TINY_RLE,
            True,
            False,
            1,
        )
        raw_bytes = self.encoding.build_line_packets(
            [1, 0, 1, 0, 1, 0, 1, 0],
            8,
            9,
            self.types.ImageEncoding.TINY_RLE,
            True,
            False,
            1,
        )
        self.assertIn(bytes([0xBF]), rle_bytes)
        self.assertIn(bytes([0xA2]), raw_bytes)
        self.assertGreaterEqual(rle_bytes.count(bytes([0xBD])), 1)


if __name__ == "__main__":
    unittest.main()
