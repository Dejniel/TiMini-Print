from __future__ import annotations

from dataclasses import replace
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import SpeedProfile
from timiniprint.protocol import PageFlow, PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ToPrintTsplProtocolTests(unittest.TestCase):
    def test_p1_profile_builds_source_ordered_tspl_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("toprint_tspl_p1")
        profile = replace(
            device.profile,
            print_defaults=replace(
                device.profile.print_defaults,
                speed=SpeedProfile(image=4, text=3),
            ),
        )
        device = replace(device, profile=profile)
        raster = RasterBuffer(
            pixels=[
                1, 0, 0, 0, 0, 0, 0, 0,
                0, 1, 0, 0, 0, 0, 0, 0,
            ],
            width=8,
            pixel_format=PixelFormat.BW1,
        )

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=3,
        )

        expected_order = (
            b"\x10\xff\x10\x03\x02",
            b"SIZE 1 mm,0.25 mm\r\n",
            b"DIRECTION 0,0\r\n",
            b"GAP 3 mm,0 mm\r\n",
            b"SET RIBBON OFF\r\n",
            b"DENSITY 9\r\n",
            b"REFERENCE 0,0\r\n",
            b"SPEED 4\r\n",
            b"CLS\r\n",
            b"BITMAP 0,0,1,2,0,\x80@\r\n",
            b"PRINT 1,1\r\n",
        )
        positions = [job.payload.index(marker) for marker in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_p1_plain_mode_uses_continuous_media_recipe(self) -> None:
        device = PrinterCatalog.load().device_from_profile("toprint_tspl_p1")
        raster = RasterBuffer(pixels=[0] * 64, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            paper_mode=PaperMode.PLAIN,
        )

        self.assertTrue(job.payload.startswith(b"\x10\xff\x10\x03\x01"))
        self.assertIn(b"SIZE 1 mm,6 mm\r\n", job.payload)
        self.assertIn(b"GAP 0 mm,0 mm\r\n", job.payload)
        self.assertNotIn(b"SPEED ", job.payload)

    def test_p1_black_mark_mode_uses_hole_sensor_and_bline(self) -> None:
        device = PrinterCatalog.load().device_from_profile("toprint_tspl_p1")
        raster = RasterBuffer(pixels=[0] * 64, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            paper_mode=PaperMode.BLACK_TAG,
        )

        self.assertTrue(job.payload.startswith(b"\x10\xff\x10\x03\x03"))
        self.assertIn(b"BLINE 3 mm,0 mm\r\n", job.payload)
        self.assertNotIn(b"GAP ", job.payload)

    def test_p1_continuous_chunk_omits_intermediate_height_padding(self) -> None:
        device = PrinterCatalog.load().device_from_profile("toprint_tspl_p1")
        raster = RasterBuffer(pixels=[0] * 64, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=True,
            paper_mode=PaperMode.PLAIN,
            page_index=1,
            page_count=2,
            page_flow=PageFlow.CONTINUOUS,
        )

        self.assertIn(b"SIZE 1 mm,1 mm\r\n", job.payload)

    def test_profiles_use_toprint_protocol_contracts(self) -> None:
        catalog = PrinterCatalog.load()
        p1 = catalog.device_from_profile("toprint_tspl_p1")

        self.assertEqual(p1.protocol_family, ProtocolFamily.TOPRINT_TSPL)
        self.assertEqual(p1.protocol_variant, "p1")
        self.assertEqual(
            p1.image_pipeline.encoding,
            ImageEncoding.TOPRINT_TSPL_BITMAP,
        )
        self.assertTrue(p1.profile.use_spp)


if __name__ == "__main__":
    unittest.main()
