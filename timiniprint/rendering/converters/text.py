from __future__ import annotations

from typing import List, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from .base import Page, PageConverter, PageSource
from ..fonts import find_monospace_bold_font, load_font

COLUMNS_PER_WIDTH = 35 / 384
REFERENCE_PATTERN = "M.I"
DEFAULT_TEXT_PAGE_HEIGHT_TO_WIDTH = 1.5


class TextConverter(PageConverter):
    def __init__(
        self,
        font_path: Optional[str] = None,
        columns: Optional[int] = None,
        wrap_lines: bool = True,
        page_height_to_width: float | None = None,
    ) -> None:
        self._font_path = font_path
        self._columns_override = columns
        self._word_wrap = wrap_lines
        self._page_height_to_width = max(
            0.1,
            float(
                DEFAULT_TEXT_PAGE_HEIGHT_TO_WIDTH
                if page_height_to_width is None
                else page_height_to_width
            ),
        )

    def open(self, path: str, width: int) -> PageSource:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return self.open_text(handle.read(), width)

    def open_text(self, text: str, width: int) -> PageSource:
        return self._render_text_pages(text.replace("\t", "    "), width)

    def _render_text_pages(self, text: str, width: int) -> PageSource:
        font = self._fit_truetype_font(
            self._font_path or find_monospace_bold_font(),
            width,
            self._reference_text(self._columns_for_width(width)),
        )
        line_height = self._font_line_height(font)
        return _TextPageSource(
            lines=self._wrap_text_lines(text, width, font),
            width=width,
            font=font,
            line_height=line_height,
            lines_per_page=self._lines_per_page(width, line_height),
        )

    @staticmethod
    def _render_text_page(
        width: int,
        lines: Sequence[str],
        font: ImageFont.FreeTypeFont,
        line_height: int,
    ) -> Image.Image:
        height = max(1, line_height * len(lines))
        img = Image.new("1", (width, height), 1)
        draw = ImageDraw.Draw(img)
        y = 0
        for line in lines:
            draw.text((0, y), line, font=font, fill=0)
            y += line_height
        return img

    def _lines_per_page(self, width: int, line_height: int) -> int:
        return max(1, int((width * self._page_height_to_width) // max(1, line_height)))

    @staticmethod
    def default_columns_for_width(width: int) -> int:
        return max(1, int(round(width * COLUMNS_PER_WIDTH)))

    def _columns_for_width(self, width: int) -> int:
        if self._columns_override is not None:
            return max(1, int(self._columns_override))
        return self.default_columns_for_width(width)

    def _reference_text(self, columns: int) -> str:
        if columns <= 0:
            return REFERENCE_PATTERN
        repeats = (columns // len(REFERENCE_PATTERN)) + 1
        return (REFERENCE_PATTERN * repeats)[:columns]

    @staticmethod
    def _fit_truetype_font(path: Optional[str], width: int, reference_text: str) -> ImageFont.FreeTypeFont:
        if not path:
            return ImageFont.load_default()
        low = 6
        high = 80
        best = None
        sample = reference_text or "M"
        while low <= high:
            size = (low + high) // 2
            font = load_font(path, size)
            if TextConverter._text_width(font, sample) <= width:
                best = font
                low = size + 1
            else:
                high = size - 1
        if best is None:
            return load_font(path, 6)
        return best

    def _wrap_text_lines(self, text: str, width: int, font: ImageFont.FreeTypeFont) -> List[str]:
        if text == "":
            return [""]
        lines: List[str] = []
        raw_lines = text.splitlines()
        if text.endswith("\n"):
            raw_lines.append("")
        for raw_line in raw_lines:
            if raw_line == "":
                lines.append("")
                continue
            lines.extend(self._wrap_line_by_width(raw_line, width, font, word_wrap=self._word_wrap))
        return lines

    def _wrap_line_by_width(
        self,
        line: str,
        width: int,
        font: ImageFont.FreeTypeFont,
        word_wrap: bool = True,
    ) -> List[str]:
        if self._text_width(font, line) <= width:
            return [line]
        lines: List[str] = []
        remaining = line
        while remaining:
            if self._text_width(font, remaining) <= width:
                lines.append(remaining)
                break
            cut = self._fit_substring_length(remaining, width, font)
            if cut <= 0:
                lines.append(remaining[:1])
                remaining = remaining[1:]
                continue
            slice_text = remaining[:cut]
            if word_wrap:
                split_at = slice_text.rfind(" ")
                if split_at > 0:
                    lines.append(slice_text[:split_at])
                    remaining = remaining[split_at + 1 :]
                    continue
            lines.append(slice_text)
            remaining = remaining[cut:]
        return lines

    def _fit_substring_length(
        self,
        text: str,
        width: int,
        font: ImageFont.FreeTypeFont,
    ) -> int:
        low = 0
        high = len(text)
        best = 0
        while low <= high:
            mid = (low + high) // 2
            if mid == 0:
                low = 1
                continue
            if self._text_width(font, text[:mid]) <= width:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    @staticmethod
    def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
        if hasattr(font, "getlength"):
            return int(font.getlength(text))
        if hasattr(font, "getbbox"):
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]
        return font.getsize(text)[0]

    @staticmethod
    def _font_line_height(font: ImageFont.FreeTypeFont) -> int:
        if hasattr(font, "getmetrics"):
            ascent, descent = font.getmetrics()
            return ascent + descent
        if hasattr(font, "getbbox"):
            bbox = font.getbbox("Ag")
            return bbox[3] - bbox[1]
        return font.getsize("Ag")[1]


class _TextPageSource(PageSource):
    def __init__(
        self,
        lines: Sequence[str],
        width: int,
        font: ImageFont.FreeTypeFont,
        line_height: int,
        lines_per_page: int,
    ) -> None:
        self._lines = list(lines)
        self._width = width
        self._font = font
        self._line_height = max(1, line_height)
        self._lines_per_page = max(1, lines_per_page)

    @property
    def page_count(self) -> int:
        return max(1, (len(self._lines) + self._lines_per_page - 1) // self._lines_per_page)

    def page(self, index: int) -> Page:
        if not self._lines:
            if index != 0:
                raise IndexError(index)
            return Page(
                TextConverter._render_text_page(
                    self._width,
                    [],
                    self._font,
                    self._line_height,
                ),
                dither=False,
                is_text=True,
            )
        if index < 0 or index >= self.page_count:
            raise IndexError(index)
        start = index * self._lines_per_page
        return Page(
            TextConverter._render_text_page(
                self._width,
                self._lines[start : start + self._lines_per_page],
                self._font,
                self._line_height,
            ),
            dither=False,
            is_text=True,
        )
