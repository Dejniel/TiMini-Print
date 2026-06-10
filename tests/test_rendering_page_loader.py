from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.rendering.converters import Page, PageLoader
from timiniprint.rendering.converters.base import ListPageSource, PageConverter


class _DummyConverter(PageConverter):
    def __init__(self, name: str) -> None:
        self._name = name

    def open(self, path: str, width: int):
        _ = path, width
        return ListPageSource([
            Page(Image.new("L", (4, 4), 255), dither=self._name == "img", is_text=self._name == "txt")
        ])


class _PdfDocument:
    page_count = 1

    def render_page(self, index: int, scale: float):
        self.rendered = (index, scale)
        return Image.new("L", (10, 10), 255)

    def close(self) -> None:
        self.closed = True


class _PdfRenderer:
    def open(self, path: str):
        self.path = path
        self.document = _PdfDocument()
        return self.document


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

    def test_default_pdf_converter_uses_injected_renderer(self) -> None:
        renderer = _PdfRenderer()
        loader = PageLoader(pdf_renderer=renderer)
        pages = loader.load("file.pdf", 8)

        self.assertEqual(renderer.path, "file.pdf")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].image.width, 8)
        self.assertEqual(renderer.document.rendered, (0, 200 / 72.0))
        self.assertTrue(renderer.document.closed)


if __name__ == "__main__":
    unittest.main()
