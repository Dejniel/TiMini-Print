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
        if self.mode == DitherMode.COLUMN_FLOYD_STEINBERG:
            return self._column_floyd_steinberg(img)
        if self.mode == DitherMode.RED_CHANNEL_3_8:
            return self._red_channel_3_8(img)
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
        if mode in {
            DitherMode.COLUMN_FLOYD_STEINBERG,
            DitherMode.RED_CHANNEL_3_8,
        }:
            return None
        raise ValueError(f"Unsupported dither mode: {mode.value}")

    def _column_floyd_steinberg(self, img: "Image.Image") -> "Image.Image":
        rgb = img.convert("RGB")
        width, height = rgb.size
        values = [
            ((38 * red) + (75 * green) + (15 * blue)) >> 7
            for red, green, blue in rgb.get_flattened_data()
        ]
        output = [255] * (width * height)
        for x in range(width):
            for y in range(height):
                index = (y * width) + x
                value = values[index]
                quantized = 0 if value < 128 else 255
                output[index] = quantized
                error = value - quantized
                self._add_error(values, width, height, x, y + 1, error, 7, 16)
                self._add_error(values, width, height, x + 1, y - 1, error, 3, 16)
                self._add_error(values, width, height, x + 1, y, error, 5, 16)
                self._add_error(values, width, height, x + 1, y + 1, error, 1, 16)
        return self._bw_image(rgb.size, output)

    def _red_channel_3_8(self, img: "Image.Image") -> "Image.Image":
        rgb = img.convert("RGB")
        width, height = rgb.size
        values = [red for red, _green, _blue in rgb.get_flattened_data()]
        output = [255] * (width * height)
        for y in range(height):
            for x in range(width):
                index = (y * width) + x
                value = values[index]
                quantized = 0 if value < 128 else 255
                output[index] = quantized
                error = value - quantized
                if x < width - 1 and y < height - 1:
                    self._add_error(
                        values, width, height, x + 1, y, error, 3, 8, clamp=False
                    )
                    self._add_error(
                        values, width, height, x, y + 1, error, 3, 8, clamp=False
                    )
                    self._add_error(
                        values, width, height, x + 1, y + 1, error, 1, 4, clamp=False
                    )
                elif x == width - 1 and y < height - 1:
                    self._add_error(
                        values, width, height, x, y + 1, error, 3, 8, clamp=False
                    )
                elif x < width - 1:
                    self._add_error(
                        values, width, height, x + 1, y, error, 1, 4, clamp=False
                    )
        return self._bw_image(rgb.size, output)

    @staticmethod
    def _add_error(
        values: list[int],
        width: int,
        height: int,
        x: int,
        y: int,
        error: int,
        numerator: int,
        denominator: int,
        *,
        clamp: bool = True,
    ) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        delta = int((error * numerator) / denominator)
        index = (y * width) + x
        value = values[index] + delta
        values[index] = max(0, min(255, value)) if clamp else value

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
