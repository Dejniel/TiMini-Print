from __future__ import annotations

from .base import ListPageSource, Page, PageSource, RasterConverter


class ImageConverter(RasterConverter):
    def open(self, path: str, width: int) -> PageSource:
        img = self._load_image(path)
        img = self._normalize_image(img)
        img = self._maybe_trim_margins(img)
        img = self._resize_to_width(img, width)
        return ListPageSource([Page(img, dither=True, is_text=False)])
