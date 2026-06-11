from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageFont

from timiniprint.rendering.converters.text import (
    _CharWidthFontMetrics,
    _PixelWidthTextWrapper,
    DEFAULT_TEXT_PAGE_HEIGHT_TO_WIDTH,
    REFERENCE_PATTERN,
    TextConverter,
)


class RenderingTextConverterTests(unittest.TestCase):
    def test_default_columns_and_reference(self) -> None:
        self.assertGreater(TextConverter.default_columns_for_width(384), 1)
        c = TextConverter(columns=12)
        self.assertEqual(c._columns_for_width(200), 12)
        ref = c._reference_text(7)
        self.assertEqual(len(ref), 7)
        self.assertTrue(all(ch in REFERENCE_PATTERN for ch in ref))

    def test_wrap_text_lines_handles_empty_and_trailing_newline(self) -> None:
        wrapper = _PixelWidthTextWrapper(
            _CharWidthFontMetrics(ImageFont.load_default()),
            100,
            word_wrap=True,
        )

        self.assertEqual(wrapper.wrapped_lines(""), [""])
        out = wrapper.wrapped_lines("a\n")
        self.assertEqual(out, ["a", ""])

    def test_wrap_line_by_width_word_wrap_modes(self) -> None:
        metrics = _CharWidthFontMetrics(ImageFont.load_default())
        line = "hello world from tests"
        wrapped = _PixelWidthTextWrapper(metrics, 25, word_wrap=True).wrap_line(line)
        hard = _PixelWidthTextWrapper(metrics, 25, word_wrap=False).wrap_line(line)

        self.assertGreaterEqual(len(wrapped), 2)
        self.assertGreaterEqual(len(hard), 2)

    def test_wrap_line_falls_back_to_single_char_chunks(self) -> None:
        wrapper = _PixelWidthTextWrapper(
            _CharWidthFontMetrics(ImageFont.load_default()),
            1,
            word_wrap=False,
        )

        self.assertEqual("".join(wrapper.wrap_line("abcdef")), "abcdef")

    def test_font_metrics_cache_reuses_repeated_char_widths(self) -> None:
        metrics = _CharWidthFontMetrics(ImageFont.load_default())
        with patch.object(
            _CharWidthFontMetrics,
            "measure_char_width",
            wraps=_CharWidthFontMetrics.measure_char_width,
        ) as measure:
            metrics.text_width("abca")
            metrics.text_width("abc")
            metrics.text_width("d")

        self.assertEqual([call.args[1] for call in measure.call_args_list], ["a", "b", "c", "d"])

    def test_load_returns_single_text_page(self) -> None:
        conv = TextConverter(font_path=None, page_height_to_width=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello\tworld", encoding="utf-8")
            with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()):
                pages = conv.load(str(path), 120)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].is_text)
        self.assertFalse(pages[0].dither)

    def test_load_splits_long_text_into_line_pages(self) -> None:
        conv = TextConverter(font_path=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.txt"
            path.write_text("\n".join(str(index) for index in range(13)), encoding="utf-8")
            with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()), patch.object(
                _CharWidthFontMetrics,
                "measure_line_height",
                return_value=10,
            ):
                pages = conv.load(str(path), 20)

        self.assertEqual(DEFAULT_TEXT_PAGE_HEIGHT_TO_WIDTH, 1.5)
        self.assertEqual(len(pages), 5)
        self.assertEqual([page.image.height for page in pages], [30, 30, 30, 30, 10])
        self.assertTrue(all(page.is_text and not page.dither for page in pages))

    def test_page_height_ratio_can_be_overridden(self) -> None:
        conv = TextConverter(font_path=None, page_height_to_width=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.txt"
            path.write_text("\n".join(str(index) for index in range(13)), encoding="utf-8")
            with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()), patch.object(
                _CharWidthFontMetrics,
                "measure_line_height",
                return_value=10,
            ):
                pages = conv.load(str(path), 20)

        self.assertEqual(len(pages), 3)
        self.assertEqual([page.image.height for page in pages], [60, 60, 10])

    def test_rotated_text_pages_keep_output_width_and_shrink_to_page_content(self) -> None:
        conv = TextConverter(
            font_path=None,
            page_height_to_width=1.5,
            rotate_90_clockwise=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotated.txt"
            path.write_text("\n".join(str(index) for index in range(5)), encoding="utf-8")
            with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()), patch.object(
                _CharWidthFontMetrics,
                "measure_line_height",
                return_value=10,
            ):
                pages = conv.load(str(path), 20)

        self.assertEqual(len(pages), 3)
        self.assertEqual([page.image.width for page in pages], [20, 20, 20])
        self.assertTrue(all(1 <= page.image.height < 30 for page in pages))
        self.assertTrue(all(page.is_text and not page.dither for page in pages))

    def test_rotated_text_applies_margin_before_rotation(self) -> None:
        conv = TextConverter(
            font_path=None,
            columns=35,
            rotate_90_clockwise=True,
        )

        with patch.object(TextConverter, "_fit_truetype_font", return_value=ImageFont.load_default()):
            page = conv.open_text("encoding='UTF-8' standalone='yes'", 384).page(0)

        bbox = page.image.convert("L").point(lambda pixel: 255 if pixel < 245 else 0).getbbox()
        self.assertEqual(page.image.width, 384)
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[1], 0)
        self.assertGreater(page.image.height - bbox[3], 0)


if __name__ == "__main__":
    unittest.main()
