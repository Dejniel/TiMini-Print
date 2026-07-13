from __future__ import annotations

from typing import TYPE_CHECKING

from ..raster import DitherMode

if TYPE_CHECKING:
    from PIL import Image


class Ditherer:
    def __init__(self, mode: DitherMode) -> None:
        self.mode = mode
        self._render = self._render_method(mode)

    def render_bw(self, img: "Image.Image") -> "Image.Image":
        return self._render(img.convert("L"))

    def _render_method(self, mode: DitherMode):
        if mode == DitherMode.NONE:
            return self._threshold
        if mode == DitherMode.FLOYD_STEINBERG:
            return self._floyd_steinberg
        if mode == DitherMode.BAYER_4:
            return self._bayer_4
        if mode == DitherMode.BAYER_8:
            return self._bayer_8
        if mode == DitherMode.ATKINSON:
            return self._atkinson
        raise ValueError(f"Unsupported dither mode: {mode.value}")

    def _floyd_steinberg(self, gray: "Image.Image") -> "Image.Image":
        return gray.convert("1", dither=self._pillow_floyd_steinberg())

    def _bayer_4(self, gray: "Image.Image") -> "Image.Image":
        return self._ordered(gray, self._bayer_matrix(4))

    def _bayer_8(self, gray: "Image.Image") -> "Image.Image":
        return self._ordered(gray, self._bayer_matrix(8))

    def _threshold(self, gray: "Image.Image") -> "Image.Image":
        data = list(gray.get_flattened_data())
        avg = sum(data) / len(data) if data else 0
        threshold = int(max(0, min(255, avg - 13)))
        return self._bw_image(gray.size, [0 if p <= threshold else 255 for p in data])

    def _ordered(self, gray: "Image.Image", matrix: list[list[int]]) -> "Image.Image":
        size = len(matrix)
        scale = 255.0 / (size * size)
        return self._bw_image(
            gray.size,
            [
                0 if gray.getpixel((x, y)) <= (matrix[y % size][x % size] + 0.5) * scale else 255
                for y in range(gray.height)
                for x in range(gray.width)
            ],
        )

    def _atkinson(self, gray: "Image.Image") -> "Image.Image":
        width, height = gray.size
        values = [float(value) for value in gray.get_flattened_data()]
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
        return self._bw_image(gray.size, [int(value) for value in values])

    def _bayer_matrix(self, size: int) -> list[list[int]]:
        if size == 2:
            return [[0, 2], [3, 1]]
        half = self._bayer_matrix(size // 2)
        return [
            *[
                [(4 * value) + 0 for value in row] + [(4 * value) + 2 for value in row]
                for row in half
            ],
            *[
                [(4 * value) + 3 for value in row] + [(4 * value) + 1 for value in row]
                for row in half
            ],
        ]

    def _bw_image(self, size: tuple[int, int], pixels: list[int]) -> "Image.Image":
        from PIL import Image

        out = Image.new("1", size)
        out.putdata(pixels)
        return out

    def _pillow_floyd_steinberg(self) -> int:
        from PIL import Image

        if hasattr(Image, "Dither"):
            return Image.Dither.FLOYDSTEINBERG
        return Image.FLOYDSTEINBERG
