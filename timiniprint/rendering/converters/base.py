from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PIL import Image, ImageOps


@dataclass(frozen=True)
class Page:
    image: Image.Image
    dither: bool
    is_text: bool


class PageConverter:
    def load(self, path: str, width: int) -> List[Page]:
        raise NotImplementedError


class RasterConverter(PageConverter):
    def __init__(
        self,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
    ) -> None:
        self._trim_side_margins = trim_side_margins
        self._trim_top_bottom_margins = trim_top_bottom_margins

    @staticmethod
    def _load_image(path: str) -> Image.Image:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            return img.copy()

    @staticmethod
    def _normalize_image(img: Image.Image) -> Image.Image:
        if img.mode not in ("RGB", "L"):
            return img.convert("RGB")
        return img

    @staticmethod
    def _resize_to_width(img: Image.Image, width: int) -> Image.Image:
        if img.width == width:
            return img
        ratio = width / float(img.width)
        height = max(1, int(img.height * ratio))
        return img.resize((width, height), Image.LANCZOS)

    def _maybe_trim_margins(self, img: Image.Image) -> Image.Image:
        if not (self._trim_side_margins or self._trim_top_bottom_margins):
            return img
        return self._trim_margins_image(img)

    def _trim_margins_image(self, img: Image.Image, threshold: int = 245) -> Image.Image:
        if img.width <= 2:
            return img
        gray = img.convert("L")
        mask = gray.point(lambda p: 255 if p < threshold else 0, mode="L")
        bbox = mask.getbbox()
        if not bbox:
            return img
        left, top, right, bottom = bbox
        new_left = left if self._trim_side_margins else 0
        new_right = right if self._trim_side_margins else img.width
        new_top = top if self._trim_top_bottom_margins else 0
        new_bottom = bottom if self._trim_top_bottom_margins else img.height
        if new_right - new_left < 2:
            new_left, new_right = 0, img.width
        if new_bottom - new_top < 2:
            new_top, new_bottom = 0, img.height
        if (
            new_left == 0
            and new_right == img.width
            and new_top == 0
            and new_bottom == img.height
        ):
            return img
        return img.crop((new_left, new_top, new_right, new_bottom))
