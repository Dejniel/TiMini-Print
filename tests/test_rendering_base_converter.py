from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.converters.base import RasterConverter


class RenderingBaseConverterTests(unittest.TestCase):
    def test_normalize_image_converts_unknown_mode(self) -> None:
        img = Image.new("P", (4, 4))
        normalized = RasterConverter._normalize_image(img)
        self.assertEqual(normalized.mode, "RGB")

    def test_resize_to_width_keeps_ratio(self) -> None:
        img = Image.new("RGB", (10, 20))
        resized = RasterConverter._resize_to_width(img, 5)
        self.assertEqual(resized.size, (5, 10))

    def test_resize_noop_when_width_unchanged(self) -> None:
        img = Image.new("RGB", (8, 6))
        same = RasterConverter._resize_to_width(img, 8)
        self.assertIs(same, img)

    def test_trim_margins_respects_flags(self) -> None:
        img = Image.new("RGB", (6, 6), (255, 255, 255))
        for x in range(2, 4):
            for y in range(1, 5):
                img.putpixel((x, y), (0, 0, 0))
        conv = RasterConverter(trim_side_margins=True, trim_top_bottom_margins=False)
        trimmed = conv._trim_margins_image(img)
        self.assertLess(trimmed.width, img.width)
        self.assertEqual(trimmed.height, img.height)

    def test_maybe_trim_disabled(self) -> None:
        img = Image.new("RGB", (6, 6), (255, 255, 255))
        conv = RasterConverter(trim_side_margins=False, trim_top_bottom_margins=False)
        out = conv._maybe_trim_margins(img)
        self.assertIs(out, img)


if __name__ == "__main__":
    unittest.main()
