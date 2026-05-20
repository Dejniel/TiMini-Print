from __future__ import annotations

import unittest

from timiniprint.printing.debug_markers import apply_debug_row_markers
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class DebugInputsTests(unittest.TestCase):
    def test_apply_debug_row_markers_adds_side_ticks(self) -> None:
        raster = RasterBuffer(
            pixels=[0] * (16 * 12),
            width=16,
            pixel_format=PixelFormat.BW1,
        )
        marked = apply_debug_row_markers(RasterSet.from_single(raster), 10)

        output = marked.require(PixelFormat.BW1)
        self.assertEqual(output.width, 16)
        self.assertEqual(output.height, 12)
        self.assertEqual(output.pixels[0:16], [1] * 16)
        row_1 = output.pixels[16:32]
        self.assertEqual(row_1[0], 1)
        self.assertEqual(row_1[-1], 1)
        self.assertEqual(sum(row_1), 2)
        row_10 = output.pixels[160:176]
        self.assertGreater(sum(row_10), 2)

    def test_apply_debug_row_markers_uses_black_values_for_gray_formats(self) -> None:
        raster = RasterBuffer(
            pixels=[0] * (8 * 2),
            width=8,
            pixel_format=PixelFormat.GRAY4,
        )
        marked = apply_debug_row_markers(RasterSet.from_single(raster), 1)

        self.assertEqual(max(marked.require(PixelFormat.GRAY4).pixels), 15)


if __name__ == "__main__":
    unittest.main()
