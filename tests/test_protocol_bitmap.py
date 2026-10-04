from __future__ import annotations

import unittest
import zlib

from timiniprint.protocol.families.bitmap import build_zlib_raster_frame
from timiniprint.raster import PixelFormat, RasterBuffer


class ProtocolBitmapTests(unittest.TestCase):
    def test_zlib_raster_frame_supports_selected_window_size(self) -> None:
        raster = RasterBuffer(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            pixel_format=PixelFormat.BW1,
        )

        for window_bits, header in ((10, b"\x28\x91"), (14, b"\x68\x81")):
            with self.subTest(window_bits=window_bits):
                frame = build_zlib_raster_frame(
                    raster,
                    command=b"\x1f\x10",
                    window_bits=window_bits,
                )

                self.assertEqual(frame[:6], b"\x1f\x10\x00\x01\x00\x01")
                compressed_length = int.from_bytes(frame[6:10], "big")
                compressed = frame[10:]
                self.assertEqual(compressed_length, len(compressed))
                self.assertEqual(compressed[:2], header)
                self.assertEqual(
                    zlib.decompress(compressed, wbits=window_bits),
                    b"\xaa",
                )

    def test_zlib_command_prefix_does_not_change_packing_or_frame_dimensions(self):
        raster = RasterBuffer(
            [1, 0, 1, 0, 0, 0, 0, 0, 1, 0,
             0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
            10, PixelFormat.BW1,
        )
        ordinary = build_zlib_raster_frame(raster, command=b"\x1f\x10")
        eleph = build_zlib_raster_frame(
            raster, command=b"\x1b#!", window_bits=10, level=-1, memory_level=9,
        )
        for frame, command in ((ordinary, b"\x1f\x10"), (eleph, b"\x1b#!")):
            with self.subTest(command=command):
                header = len(command)
                self.assertEqual(frame[:header + 4], command + b"\x00\x02\x00\x02")
                compressed = frame[header + 8:]
                self.assertEqual(int.from_bytes(frame[header + 4:header + 8], "big"),
                                 len(compressed))
                self.assertEqual(zlib.decompress(compressed), b"\xa0\x80\x10\x40")
        compressor = zlib.compressobj(level=6, wbits=10, memLevel=8)
        expected = compressor.compress(b"\xa0\x80\x10\x40") + compressor.flush()
        self.assertEqual(ordinary[10:], expected)


if __name__ == "__main__":
    unittest.main()
