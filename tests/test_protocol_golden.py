from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub, load_golden_hex
from timiniprint.protocol.family import ProtocolFamily


class ProtocolGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.builders = importlib.import_module("timiniprint.protocol._builders")
        cls.types = importlib.import_module("timiniprint.protocol.types")
        cls.raster = importlib.import_module("timiniprint.raster")
        cls.golden = load_golden_hex("tests/fixtures/protocol_golden.json")

    def test_golden_old_format(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(self.raster.PixelFormat.BW1,),
                encoding=self.types.ImageEncoding.TINY_RAW,
            ),
        )
        self.assertEqual(data.hex(), self.golden["image_old"])

    def test_golden_prefixed_tiny_format(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 1, 0, 0, 1, 1, 0, 0],
            width=8,
            is_text=True,
            speed=8,
            energy=8000,
            density=None,
            blackening=4,
            lsb_first=False,
            protocol_family=ProtocolFamily.TINY_PREFIXED,
            feed_padding=7,
            dev_dpi=300,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(self.raster.PixelFormat.BW1,),
                encoding=self.types.ImageEncoding.TINY_RAW,
            ),
        )
        self.assertEqual(data.hex(), self.golden["text_new"])

    def test_golden_compress_fallback_raw(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=9,
            energy=6000,
            density=None,
            blackening=2,
            lsb_first=False,
            protocol_family=ProtocolFamily.TINY,
            feed_padding=6,
            dev_dpi=203,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(self.raster.PixelFormat.BW1,),
                encoding=self.types.ImageEncoding.TINY_RLE,
            ),
        )
        self.assertEqual(data.hex(), self.golden["compress_fallback_raw"])

    def test_golden_v5x_single_row(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5X,
            feed_padding=12,
            dev_dpi=203,
            can_print_label=True,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(
                    self.raster.PixelFormat.BW1,
                    self.raster.PixelFormat.GRAY4,
                    self.raster.PixelFormat.GRAY8,
                ),
                encoding=self.types.ImageEncoding.V5X_DOT,
            ),
        )
        self.assertEqual(data.hex(), self.golden["v5x_single_row"])

    def test_golden_v5x_gray_single_row(self) -> None:
        data = self.builders._build_job_from_raster_set(
            raster_set=self.raster.RasterSet(
                rasters={
                    self.raster.PixelFormat.BW1: self.raster.RasterBuffer(
                        pixels=[1, 0, 1, 0, 1, 0, 1, 0],
                        width=8,
                        pixel_format=self.raster.PixelFormat.BW1,
                    ),
                    self.raster.PixelFormat.GRAY4: self.raster.RasterBuffer(
                        pixels=[15, 14, 13, 12, 11, 10, 9, 8],
                        width=8,
                        pixel_format=self.raster.PixelFormat.GRAY4,
                    ),
                }
            ),
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5X,
            feed_padding=12,
            dev_dpi=203,
            can_print_label=True,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(
                    self.raster.PixelFormat.GRAY4,
                    self.raster.PixelFormat.GRAY8,
                    self.raster.PixelFormat.BW1,
                ),
                encoding=self.types.ImageEncoding.V5X_GRAY,
            ),
        )
        self.assertEqual(data.hex(), self.golden["v5x_gray_single_row"])

    def test_golden_v5c_single_row(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=True,
            speed=10,
            energy=5000,
            density=None,
            blackening=4,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5C,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.types.ImagePipelineConfig(
                formats=(self.raster.PixelFormat.BW1,),
                encoding=self.types.ImageEncoding.V5C_A4,
            ),
        )
        self.assertEqual(data.hex(), self.golden["v5c_single_row"])


if __name__ == "__main__":
    unittest.main()
