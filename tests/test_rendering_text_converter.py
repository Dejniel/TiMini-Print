from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageFont

from timiniprint.rendering.converters.text import REFERENCE_PATTERN, TextConverter


class RenderingTextConverterTests(unittest.TestCase):
    def test_default_columns_and_reference(self) -> None:
        self.assertGreater(TextConverter.default_columns_for_width(384), 1)
        c = TextConverter(columns=12)
        self.assertEqual(c._columns_for_width(200), 12)
        ref = c._reference_text(7)
        self.assertEqual(len(ref), 7)
        self.assertTrue(all(ch in REFERENCE_PATTERN for ch in ref))

    def test_wrap_text_lines_handles_empty_and_trailing_newline(self) -> None:
        conv = TextConverter()
        font = ImageFont.load_default()
        self.assertEqual(conv._wrap_text_lines("", 100, font), [""])
        out = conv._wrap_text_lines("a\n", 100, font)
        self.assertEqual(out, ["a", ""])

    def test_wrap_line_by_width_word_wrap_modes(self) -> None:
        conv = TextConverter()
        font = ImageFont.load_default()
        line = "hello world from tests"
        wrapped = conv._wrap_line_by_width(line, 25, font, word_wrap=True)
        hard = conv._wrap_line_by_width(line, 25, font, word_wrap=False)
        self.assertGreaterEqual(len(wrapped), 2)
        self.assertGreaterEqual(len(hard), 2)

    def test_fit_substring_fallback(self) -> None:
        conv = TextConverter()
        font = ImageFont.load_default()
        cut = conv._fit_substring_length("abcdef", 1, font)
        self.assertGreaterEqual(cut, 0)

    def test_load_returns_single_text_page(self) -> None:
        conv = TextConverter(font_path=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello\tworld", encoding="utf-8")
            with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()):
                pages = conv.load(str(path), 120)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].is_text)
        self.assertFalse(pages[0].dither)


if __name__ == "__main__":
    unittest.main()
