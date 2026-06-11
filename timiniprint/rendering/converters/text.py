from __future__ import annotations

from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from .base import Page, PageConverter, PageSource
from ..fonts import find_monospace_bold_font, load_font

COLUMNS_PER_WIDTH = 35 / 384
REFERENCE_PATTERN = "M.I"
DEFAULT_TEXT_PAGE_HEIGHT_TO_WIDTH = 1.5


class _CharWidthFontMetrics:
    def __init__(self, font: ImageFont.FreeTypeFont) -> None:
        self.font = font
        self.line_height = self.measure_line_height(font)
        self._draw = ImageDraw.Draw(Image.new("1", (1, 1)))
        self._char_width_cache: dict[str, float] = {}

    def char_width(self, char: str) -> float:
        if char not in self._char_width_cache:
            self._char_width_cache[char] = self.measure_char_width(self.font, char)
        return self._char_width_cache[char]

    def text_width(self, text: str) -> float:
        return sum(self.char_width(char) for char in text)

    def rendered_text_right_edge(self, text: str) -> int:
        if not text:
            return 0
        return max(0, self._draw.textbbox((0, 0), text, font=self.font)[2])

    @staticmethod
    def measure_char_width(font: ImageFont.FreeTypeFont, char: str) -> float:
        if hasattr(font, "getlength"):
            return font.getlength(char)
        if hasattr(font, "getbbox"):
            bbox = font.getbbox(char)
            return bbox[2] - bbox[0]
        return font.getsize(char)[0]

    @staticmethod
    def measure_line_height(font: ImageFont.FreeTypeFont) -> int:
        if hasattr(font, "getmetrics"):
            ascent, descent = font.getmetrics()
            return ascent + descent
        if hasattr(font, "getbbox"):
            bbox = font.getbbox("Ag")
            return bbox[3] - bbox[1]
        return font.getsize("Ag")[1]


class _PixelWidthTextWrapper:
    def __init__(
        self,
        metrics: _CharWidthFontMetrics,
        max_width: int,
        word_wrap: bool,
    ) -> None:
        self._metrics = metrics
        self._max_width = max(1, max_width)
        self._word_wrap = word_wrap

    def wrapped_lines(self, text: str) -> list[str]:
        if text == "":
            return [""]
        lines: list[str] = []
        raw_lines = text.splitlines()
        if text.endswith("\n"):
            raw_lines.append("")
        for raw_line in raw_lines:
            if raw_line == "":
                lines.append("")
                continue
            lines.extend(self.wrap_line(raw_line))
        return lines

    def wrap_line(self, line: str) -> list[str]:
        lines: list[str] = []
        start = 0
        while start < len(line):
            end, split_at = self._fit_segment(line, start)
            if end >= len(line):
                lines.append(line[start:])
                break
            if self._word_wrap and split_at is not None:
                lines.append(line[start:split_at])
                start = split_at + 1
            else:
                lines.append(line[start:end])
                start = end
        return lines

    def _fit_segment(self, line: str, start: int) -> tuple[int, int | None]:
        width = 0.0
        split_at: int | None = None
        for index in range(start, len(line)):
            if line[index] == " " and index > start:
                split_at = index
            next_width = width + self._metrics.char_width(line[index])
            if next_width > self._max_width:
                return (index if index > start else start + 1), split_at
            width = next_width
        return len(line), split_at


class TextConverter(PageConverter):
    def __init__(
        self,
        font_path: Optional[str] = None,
        columns: Optional[int] = None,
        wrap_lines: bool = True,
        page_height_to_width: float | None = None,
        rotate_90_clockwise: bool = False,
    ) -> None:
        self._font_path = font_path
        self._columns_override = columns
        self._word_wrap = wrap_lines
        self._rotate_90_clockwise = rotate_90_clockwise
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
        return self._open_text_source(text.replace("\t", "    "), width)

    def _open_text_source(self, text: str, width: int) -> PageSource:
        render_width = self._render_width(width)
        margin = min(12, width // 64) if self._rotate_90_clockwise else 0
        usable_width = max(1, render_width - 2 * margin)
        font = self._fit_truetype_font(
            self._font_path or find_monospace_bold_font(),
            usable_width,
            self._reference_text(self._columns_for_width(usable_width)),
        )
        metrics = _CharWidthFontMetrics(font)
        lines = _PixelWidthTextWrapper(
            metrics,
            usable_width,
            self._word_wrap,
        ).wrapped_lines(text)
        return _TextPageSource(
            lines=lines,
            lines_per_page=self._lines_per_page(width, metrics.line_height, margin),
            width=render_width,
            output_width=width,
            metrics=metrics,
            margin=margin,
            rotate_90_clockwise=self._rotate_90_clockwise,
        )

    @staticmethod
    def _paint_text_page_image(
        width: int,
        lines: Sequence[str],
        font: ImageFont.FreeTypeFont,
        line_height: int,
        min_height: int = 1,
        margin: int = 0,
    ) -> Image.Image:
        height = max(1, min_height, line_height * len(lines) + 2 * margin)
        img = Image.new("1", (width, height), 1)
        draw = ImageDraw.Draw(img)
        y = margin
        for line in lines:
            draw.text((margin, y), line, font=font, fill=0)
            y += line_height
        return img

    def _lines_per_page(self, width: int, line_height: int, margin: int = 0) -> int:
        if self._rotate_90_clockwise:
            return max(1, (width - 2 * margin) // max(1, line_height))
        return max(1, int((width * self._page_height_to_width) // max(1, line_height)))

    def _render_width(self, width: int) -> int:
        if self._rotate_90_clockwise:
            return max(1, int(round(width * self._page_height_to_width)))
        return width

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
            if _CharWidthFontMetrics(font).text_width(sample) <= width:
                best = font
                low = size + 1
            else:
                high = size - 1
        if best is None:
            return load_font(path, 6)
        return best


class _TextPageSource(PageSource):
    def __init__(
        self,
        lines: Sequence[str],
        lines_per_page: int,
        width: int,
        output_width: int,
        metrics: _CharWidthFontMetrics,
        margin: int = 0,
        rotate_90_clockwise: bool = False,
    ) -> None:
        self._lines = list(lines)
        self._lines_per_page = max(1, lines_per_page)
        self._width = width
        self._output_width = max(1, output_width)
        self._metrics = metrics
        self._margin = max(0, margin)
        self._rotate_90_clockwise = rotate_90_clockwise

    @property
    def page_count(self) -> int:
        return max(1, (len(self._lines) + self._lines_per_page - 1) // self._lines_per_page)

    def page(self, index: int) -> Page:
        if not self._lines:
            if index != 0:
                raise IndexError(index)
            lines: Sequence[str] = ()
        else:
            if index < 0 or index >= self.page_count:
                raise IndexError(index)
            start = index * self._lines_per_page
            lines = self._lines[start : start + self._lines_per_page]
        img = TextConverter._paint_text_page_image(
            self._page_image_width(lines),
            lines,
            self._metrics.font,
            self._metrics.line_height,
            min_height=self._output_width if self._rotate_90_clockwise else 1,
            margin=self._margin,
        )
        if self._rotate_90_clockwise:
            img = img.transpose(Image.Transpose.ROTATE_270)
        return Page(img, dither=False, is_text=True)

    def _page_image_width(self, lines: Sequence[str]) -> int:
        if not self._rotate_90_clockwise:
            return self._width
        content_width = max((self._metrics.rendered_text_right_edge(line) for line in lines), default=0)
        return min(self._width, max(1, content_width + 2 * self._margin))
