from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.dither import render_bw_image
from timiniprint.rendering.dither import DitherMode


class RenderingDitherTests(unittest.TestCase):
    def test_none_uses_plain_threshold(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 100, 220, 255])

        self.assertEqual(
            list(render_bw_image(img, DitherMode.NONE).convert("L").getdata()),
            [0, 0, 255, 255],
        )

    def test_bayer_modes_render_black_and_white_pixels(self) -> None:
        img = Image.new("L", (8, 8), 128)

        for mode in (DitherMode.BAYER_4, DitherMode.BAYER_8):
            pixels = set(render_bw_image(img, mode).convert("L").getdata())
            self.assertEqual(pixels, {0, 255})

    def test_atkinson_renders_black_and_white_pixels(self) -> None:
        img = Image.new("L", (8, 1))
        img.putdata([0, 64, 96, 120, 136, 160, 192, 255])

        pixels = set(render_bw_image(img, DitherMode.ATKINSON).convert("L").getdata())

        self.assertEqual(pixels, {0, 255})


if __name__ == "__main__":
    unittest.main()
