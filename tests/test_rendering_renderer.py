from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.renderer import image_to_bw_pixels


class RenderingRendererTests(unittest.TestCase):
    def test_dither_mode_black_white_mapping(self) -> None:
        img = Image.new("1", (2, 1), 1)
        img.putpixel((0, 0), 0)
        out = image_to_bw_pixels(img, dither=True)
        self.assertEqual(out, [1, 0])

    def test_non_dither_threshold_from_average(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 100, 220, 255])
        out = image_to_bw_pixels(img, dither=False)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0], 1)
        self.assertEqual(out[-1], 0)


if __name__ == "__main__":
    unittest.main()
