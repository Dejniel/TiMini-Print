from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PIL import Image

from timiniprint.rendering.converters.image import ImageConverter


class RenderingImageConverterTests(unittest.TestCase):
    def test_load_pipeline_and_page_flags(self) -> None:
        source = Image.new("RGB", (10, 10))
        load_mock = Mock(return_value=source)
        converter = ImageConverter(image_loader=load_mock)
        with patch.object(
            ImageConverter, "_normalize_image", wraps=ImageConverter._normalize_image
        ) as normalize_mock, patch.object(
            ImageConverter, "_maybe_trim_margins", wraps=converter._maybe_trim_margins
        ) as trim_mock:
            pages = converter.load("dummy.png", 8)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].dither)
        self.assertFalse(pages[0].is_text)
        self.assertEqual(pages[0].image.width, 8)
        self.assertEqual(load_mock.call_count, 1)
        self.assertEqual(normalize_mock.call_count, 1)
        self.assertEqual(trim_mock.call_count, 1)

    def test_rotate_happens_before_resize_to_print_width(self) -> None:
        source = Image.new("RGB", (800, 200), "black")
        converter = ImageConverter(
            image_loader=Mock(return_value=source),
            rotation_degrees=90,
        )

        pages = converter.load("landscape.png", 384)

        self.assertEqual(pages[0].image.size, (384, 1536))

    def test_supports_counter_clockwise_quarter_turn_as_270_clockwise(self) -> None:
        source = Image.new("1", (2, 1), 1)
        source.putpixel((0, 0), 0)
        converter = ImageConverter(
            image_loader=Mock(return_value=source),
            trim_side_margins=False,
            trim_top_bottom_margins=False,
            rotation_degrees=270,
        )

        page = converter.load("landscape.png", 1)[0]

        self.assertEqual(page.image.size, (1, 2))
        self.assertEqual(
            list(page.image.getdata()),
            [(255, 255, 255), (0, 0, 0)],
        )


if __name__ == "__main__":
    unittest.main()
