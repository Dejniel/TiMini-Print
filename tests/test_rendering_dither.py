from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.dither import Ditherer
from timiniprint.rendering.dither import DitherMode


class RenderingDitherTests(unittest.TestCase):
    def test_none_uses_plain_threshold(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 100, 220, 255])

        self.assertEqual(
            list(
                Ditherer(DitherMode.NONE)
                .render_bw(img)
                .convert("L")
                .get_flattened_data()
            ),
            [0, 0, 255, 255],
        )

    def test_bayer_modes_render_black_and_white_pixels(self) -> None:
        img = Image.new("L", (8, 8), 128)

        for mode in (DitherMode.BAYER_4, DitherMode.BAYER_8):
            pixels = set(
                Ditherer(mode).render_bw(img).convert("L").get_flattened_data()
            )
            self.assertEqual(pixels, {0, 255})

    def test_atkinson_renders_black_and_white_pixels(self) -> None:
        img = Image.new("L", (8, 1))
        img.putdata([0, 64, 96, 120, 136, 160, 192, 255])

        pixels = set(
            Ditherer(DitherMode.ATKINSON)
            .render_bw(img)
            .convert("L")
            .get_flattened_data()
        )

        self.assertEqual(pixels, {0, 255})


if __name__ == "__main__":
    unittest.main()
