from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


class DitherMode(str, Enum):
    NONE = "none"
    FLOYD_STEINBERG = "floyd_steinberg"
    BAYER_4 = "bayer_4"
    BAYER_8 = "bayer_8"
    ATKINSON = "atkinson"


def render_bw_image(img: "Image.Image", mode: DitherMode) -> "Image.Image":
    from PIL import Image

    gray = img.convert("L")
    if mode == DitherMode.NONE:
        return _threshold_bw_image(gray)
    if mode == DitherMode.FLOYD_STEINBERG:
        return gray.convert("1", dither=_pillow_floyd_steinberg())
    if mode == DitherMode.BAYER_4:
        return _ordered_bw_image(gray, _bayer_matrix(4))
    if mode == DitherMode.BAYER_8:
        return _ordered_bw_image(gray, _bayer_matrix(8))
    if mode == DitherMode.ATKINSON:
        return _atkinson_bw_image(gray)
    raise ValueError(f"Unsupported dither mode: {mode.value}")


def _threshold_bw_image(gray: "Image.Image") -> "Image.Image":
    data = list(gray.getdata())
    avg = sum(data) / len(data) if data else 0
    threshold = int(max(0, min(255, avg - 13)))
    return _bw_image(gray.size, [0 if p <= threshold else 255 for p in data])


def _ordered_bw_image(gray: "Image.Image", matrix: list[list[int]]) -> "Image.Image":
    size = len(matrix)
    scale = 255.0 / (size * size)
    pixels = []
    for y in range(gray.height):
        for x in range(gray.width):
            threshold = (matrix[y % size][x % size] + 0.5) * scale
            pixels.append(0 if gray.getpixel((x, y)) <= threshold else 255)
    return _bw_image(gray.size, pixels)


def _atkinson_bw_image(gray: "Image.Image") -> "Image.Image":
    width, height = gray.size
    values = [float(value) for value in gray.getdata()]
    for y in range(height):
        for x in range(width):
            idx = (y * width) + x
            old = values[idx]
            new = 0.0 if old < 128 else 255.0
            values[idx] = new
            error = (old - new) / 8.0
            for dx, dy in ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2)):
                nx = x + dx
                ny = y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    nidx = (ny * width) + nx
                    values[nidx] = max(0.0, min(255.0, values[nidx] + error))
    return _bw_image(gray.size, [int(value) for value in values])


def _bayer_matrix(size: int) -> list[list[int]]:
    if size == 2:
        return [[0, 2], [3, 1]]
    half = _bayer_matrix(size // 2)
    top = [
        [(4 * value) + 0 for value in row] + [(4 * value) + 2 for value in row]
        for row in half
    ]
    bottom = [
        [(4 * value) + 3 for value in row] + [(4 * value) + 1 for value in row]
        for row in half
    ]
    return top + bottom


def _bw_image(size: tuple[int, int], pixels: list[int]) -> "Image.Image":
    from PIL import Image

    out = Image.new("1", size)
    out.putdata(pixels)
    return out


def _pillow_floyd_steinberg() -> int:
    from PIL import Image

    if hasattr(Image, "Dither"):
        return Image.Dither.FLOYDSTEINBERG
    return Image.FLOYDSTEINBERG
