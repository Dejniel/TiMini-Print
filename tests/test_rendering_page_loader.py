from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.converters import Page, PageLoader
from timiniprint.rendering.converters.base import PageConverter


class _DummyConverter(PageConverter):
    def __init__(self, name: str) -> None:
        self._name = name

    def load(self, path: str, width: int):
        _ = path, width
        return [Page(Image.new("L", (4, 4), 255), dither=self._name == "img", is_text=self._name == "txt")]


class RenderingPageLoaderTests(unittest.TestCase):
    def test_supported_extensions(self) -> None:
        loader = PageLoader()
        exts = loader.supported_extensions
        self.assertIn(".png", exts)
        self.assertIn(".pdf", exts)
        self.assertIn(".txt", exts)

    def test_dispatch_and_unsupported(self) -> None:
        loader = PageLoader(converters={".txt": _DummyConverter("txt")})
        pages = loader.load("file.txt", 100)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].is_text)
        with self.assertRaises(ValueError):
            loader.load("file.png", 100)


if __name__ == "__main__":
    unittest.main()
