from __future__ import annotations

import sys
import types
import unittest

from PIL import Image

from timiniprint.rendering.converters.pdf import PdfConverter


class _FakePage:
    def __init__(self, color: int = 255) -> None:
        self._color = color
        self.closed = False

    def render_topil(self, scale=1.0):
        _ = scale
        return Image.new("L", (10, 10), self._color)

    def close(self):
        self.closed = True


class _FakePdfDocument:
    def __init__(self, _path: str, pages: int = 2) -> None:
        self._pages = [_FakePage(255), _FakePage(180)][:pages]
        self.closed = False

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, idx: int):
        return self._pages[idx]

    def close(self):
        self.closed = True


class RenderingPdfConverterTests(unittest.TestCase):
    def test_select_page_indexes_valid_and_invalid(self) -> None:
        c = PdfConverter(page_selection="1,3-4")
        self.assertEqual(list(c._select_page_indexes(5)), [0, 2, 3])
        with self.assertRaises(ValueError):
            PdfConverter(page_selection="x")._select_page_indexes(5)
        with self.assertRaises(ValueError):
            PdfConverter(page_selection="4-2")._select_page_indexes(5)
        with self.assertRaises(ValueError):
            PdfConverter(page_selection="9")._select_page_indexes(5)

    def test_append_page_gap(self) -> None:
        img = Image.new("L", (10, 10), 255)
        out = PdfConverter._append_page_gap(img, 4)
        self.assertEqual(out.size, (10, 14))
        self.assertIs(PdfConverter._append_page_gap(img, 0), img)

    def test_load_pdf_pages_with_mocked_pypdfium2(self) -> None:
        fake = types.ModuleType("pypdfium2")
        fake.PdfDocument = _FakePdfDocument
        sys.modules["pypdfium2"] = fake
        c = PdfConverter(page_selection="1", page_gap_px=3)
        pages = c.load("dummy.pdf", width=8)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].image.width, 8)
        self.assertFalse(pages[0].is_text)

    def test_load_pdf_pages_raises_when_no_pages(self) -> None:
        fake = types.ModuleType("pypdfium2")
        class _EmptyDoc(_FakePdfDocument):
            def __init__(self, _path: str):
                self._pages = []
                self.closed = False
        fake.PdfDocument = _EmptyDoc
        sys.modules["pypdfium2"] = fake
        c = PdfConverter()
        with self.assertRaises(RuntimeError):
            c._load_pdf_pages("dummy.pdf")


if __name__ == "__main__":
    unittest.main()
