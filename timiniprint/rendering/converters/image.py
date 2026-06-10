from __future__ import annotations

from .base import ImageLoader, ListPageSource, Page, PageSource, RasterConverter


class ImageConverter(RasterConverter):
    def __init__(
        self,
        *,
        image_loader: ImageLoader | None = None,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
        rotate_90_clockwise: bool = False,
    ) -> None:
        super().__init__(
            trim_side_margins=trim_side_margins,
            trim_top_bottom_margins=trim_top_bottom_margins,
            rotate_90_clockwise=rotate_90_clockwise,
        )
        self._image_loader = image_loader or self.load_image

    def open(self, path: str, width: int) -> PageSource:
        img = self._image_loader(path)
        img = self._normalize_image(img)
        img = self._maybe_trim_margins(img)
        img = self._rotate_image(img)
        img = self._resize_to_width(img, width)
        return ListPageSource([Page(img, dither=True, is_text=False)])
