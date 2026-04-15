from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.converters.base import Page
from timiniprint.rendering.renderer import apply_page_transforms


class RenderingTransformTests(unittest.TestCase):
    def test_apply_page_transforms_rotates_clockwise(self) -> None:
        image = Image.new("1", (8, 16), 1)
        page = Page(image=image, dither=False, is_text=False)

        transformed = apply_page_transforms(
            [page],
            rotate_90_clockwise=True,
        )

        self.assertEqual(len(transformed), 1)
        self.assertEqual(transformed[0].image.size, (16, 8))
        self.assertFalse(transformed[0].dither)
        self.assertFalse(transformed[0].is_text)

    def test_apply_page_transforms_returns_pages_unchanged_without_rotation(self) -> None:
        image = Image.new("1", (8, 16), 1)
        page = Page(image=image, dither=True, is_text=True)

        transformed = apply_page_transforms(
            [page],
        )

        self.assertEqual(transformed, [page])


if __name__ == "__main__":
    unittest.main()
