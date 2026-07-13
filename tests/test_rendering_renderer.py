from __future__ import annotations

from io import BytesIO
import unittest

from PIL import Image

from timiniprint.rendering.dither import DitherMode
from timiniprint.raster import PixelFormat
from timiniprint.rendering.renderer import PrintImageRenderer


class RenderingRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = PrintImageRenderer()

    def test_dither_mode_black_white_mapping(self) -> None:
        img = Image.new("1", (2, 1), 1)
        img.putpixel((0, 0), 0)
        raster = self.renderer.encode(
            self.renderer.prepare(img, PixelFormat.BW1, dither_mode=DitherMode.FLOYD_STEINBERG),
            PixelFormat.BW1,
        )
        self.assertEqual(list(raster.pixels), [1, 0])

    def test_non_dither_threshold_from_average(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 100, 220, 255])
        raster = self.renderer.encode(
            self.renderer.prepare(img, PixelFormat.BW1, dither_mode=DitherMode.NONE),
            PixelFormat.BW1,
        )
        self.assertEqual(len(raster.pixels), 4)
        self.assertEqual(raster.pixels[0], 1)
        self.assertEqual(raster.pixels[-1], 0)

    def test_render_raster_set_builds_requested_formats_with_matching_dimensions(self) -> None:
        img = Image.new("L", (4, 2))
        img.putdata([0, 32, 128, 255, 16, 64, 192, 240])
        raster_set = self.renderer.raster_set(
            img,
            (PixelFormat.GRAY4, PixelFormat.GRAY8, PixelFormat.BW1),
            dither_mode=DitherMode.NONE,
            gamma_handle=False,
        )

        self.assertEqual(raster_set.width, 4)
        self.assertEqual(raster_set.height, 2)
        self.assertEqual(raster_set.require(PixelFormat.GRAY4).pixel_format, PixelFormat.GRAY4)
        self.assertEqual(raster_set.require(PixelFormat.GRAY8).pixel_format, PixelFormat.GRAY8)
        self.assertEqual(raster_set.require(PixelFormat.BW1).pixel_format, PixelFormat.BW1)

    def test_prepare_gray4_print_image_preserves_raster_values(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 32, 128, 255])
        prepared = self.renderer.prepare(
            img,
            PixelFormat.GRAY4,
            dither_mode=DitherMode.NONE,
            gamma_handle=False,
        )

        self.assertEqual(list(prepared.get_flattened_data()), [0, 32, 128, 240])
        raster = self.renderer.encode(prepared, PixelFormat.GRAY4)
        self.assertEqual(list(raster.pixels), [15, 13, 7, 0])

    def test_preview_png_uses_prepared_bw_image(self) -> None:
        img = Image.new("L", (4, 1))
        img.putdata([0, 100, 220, 255])
        preview = Image.open(
            BytesIO(
                self.renderer.preview_png(
                    img,
                    PixelFormat.BW1,
                    dither_mode=DitherMode.NONE,
                )
            )
        )

        self.assertEqual(preview.mode, "L")
        self.assertEqual(list(preview.get_flattened_data()), [0, 0, 255, 255])

    def test_preview_png_expands_gray4_visual_range(self) -> None:
        img = Image.new("L", (2, 1))
        img.putdata([0, 255])
        preview = Image.open(
            BytesIO(
                self.renderer.preview_png(
                    img,
                    PixelFormat.GRAY4,
                    dither_mode=DitherMode.NONE,
                    gamma_handle=False,
                )
            )
        )

        self.assertEqual(list(preview.get_flattened_data()), [0, 255])


if __name__ == "__main__":
    unittest.main()
