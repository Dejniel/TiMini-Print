from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from timiniprint.rendering.converters.image import ImageConverter


class RenderingImageConverterTests(unittest.TestCase):
    def test_load_pipeline_and_page_flags(self) -> None:
        converter = ImageConverter()
        source = Image.new("RGB", (10, 10))
        with patch.object(ImageConverter, "_load_image", return_value=source) as load_mock, patch.object(
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


if __name__ == "__main__":
    unittest.main()
