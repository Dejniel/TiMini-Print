from __future__ import annotations

import unittest

from timiniprint.protocol.families.bitmap import pad_raster
from timiniprint.raster import PixelFormat, RasterBuffer


class RasterPaddingTests(unittest.TestCase):
    def test_padding_preserves_rows_format_and_source(self) -> None:
        for pixel_format, pixels, fill in (
            (PixelFormat.BW1, [1, 0, 0, 1], 0),
            (PixelFormat.GRAY4, [15, 0, 3, 12], 0),
            (PixelFormat.GRAY8, [0, 255, 64, 192], 255),
        ):
            with self.subTest(pixel_format=pixel_format):
                raster = RasterBuffer(tuple(pixels), 2, pixel_format)
                self.assertIs(pad_raster(raster), raster)
                padded = pad_raster(raster, left=1, right=2, bottom=1, fill=fill)
                self.assertEqual(padded.pixel_format, pixel_format)
                self.assertEqual((padded.width, padded.height), (5, 3))
                self.assertEqual(padded.pixels, [fill, *pixels[:2], fill, fill, fill, *pixels[2:], *([fill] * 7)])
                self.assertEqual(raster.pixels, tuple(pixels))

    def test_empty_raster_and_bottom_only_padding(self) -> None:
        raster = RasterBuffer([], 3)
        padded = pad_raster(raster, bottom=2)
        self.assertEqual((padded.width, padded.height, padded.pixels), (3, 2, [0] * 6))

    def test_negative_padding_is_rejected(self) -> None:
        for side in ("left", "right", "bottom"):
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, "non-negative"):
                pad_raster(RasterBuffer([1], 1), **{side: -1})
