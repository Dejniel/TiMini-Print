from __future__ import annotations

import unittest
import zlib

from timiniprint.protocol.families.bitmap import build_1f10_zlib_raster
from timiniprint.raster import PixelFormat, RasterBuffer


class ProtocolBitmapTests(unittest.TestCase):
    def test_build_1f10_zlib_raster_supports_selected_window_size(self) -> None:
        raster = RasterBuffer(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            pixel_format=PixelFormat.BW1,
        )

        for window_bits, header in ((10, b"\x28\x91"), (14, b"\x68\x81")):
            with self.subTest(window_bits=window_bits):
                frame = build_1f10_zlib_raster(
                    raster,
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


if __name__ == "__main__":
    unittest.main()
