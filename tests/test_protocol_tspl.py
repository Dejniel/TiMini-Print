from __future__ import annotations

from dataclasses import replace
import unittest

from timiniprint.devices.profiles import SpeedProfile
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class TsplProtocolTests(unittest.TestCase):
    def test_p1_profile_builds_tspl_bitmap_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("tspl_p1")
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

        self.assertTrue(job.payload.startswith(b"\x10\xff\x10\x03\x02"))
        self.assertIn(b"SIZE 1 mm,0.25 mm\r\n", job.payload)
        self.assertIn(b"DIRECTION 0,0\r\n", job.payload)
        self.assertIn(b"GAP 3 mm,0 mm\r\n", job.payload)
        self.assertIn(b"SET RIBBON OFF\r\n", job.payload)
        self.assertIn(b"DENSITY 9\r\n", job.payload)
        self.assertIn(b"REFERENCE 0,0\r\n", job.payload)
        self.assertIn(b"SPEED 6\r\n", job.payload)
        self.assertIn(b"BITMAP 0,0,1,2,0,\x80@\r\n", job.payload)
        self.assertTrue(job.payload.endswith(b"PRINT 1,1\r\n"))

    def test_p1_profile_uses_source_backed_command_order(self) -> None:
        device = PrinterCatalog.load().device_from_profile("tspl_p1")
        profile = replace(device.profile, speed=SpeedProfile(image=4, text=3))
        device = replace(device, profile=profile)
        raster = RasterBuffer(pixels=[0] * 64, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=3,
        )

        expected_order = (
            b"\x10\xff\x10\x03\x02",
            b"SIZE ",
            b"DIRECTION 0,0\r\n",
            b"GAP ",
            b"SET RIBBON OFF\r\n",
            b"DENSITY 9\r\n",
            b"REFERENCE 0,0\r\n",
            b"SPEED 4\r\n",
            b"CLS\r\n",
            b"BITMAP ",
            b"PRINT 1,1\r\n",
        )
        positions = [job.payload.index(marker) for marker in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_p1_plain_mode_uses_continuous_media_setup_and_recipe(self) -> None:
        device = PrinterCatalog.load().device_from_profile("tspl_p1")
        profile = replace(device.profile, speed=SpeedProfile(image=4, text=3))
        device = replace(device, profile=profile)
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

    def test_catalog_detects_eleph_p1_as_tspl_without_stealing_old_p1(self) -> None:
        catalog = PrinterCatalog.load()

        eleph = catalog.detect_device("P1_F30E")
        old = catalog.detect_device("P1-1234")

        self.assertIsNotNone(eleph)
        assert eleph is not None
        self.assertEqual(eleph.profile_key, "tspl_p1")
        self.assertEqual(eleph.protocol_family.value, "tspl")
        self.assertEqual(eleph.protocol_variant, "p1")

        self.assertIsNotNone(old)
        assert old is not None
        self.assertNotEqual(old.profile_key, "tspl_p1")
        self.assertEqual(old.protocol_family.value, "legacy")


if __name__ == "__main__":
    unittest.main()
