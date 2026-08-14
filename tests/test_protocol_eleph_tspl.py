from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ElephTsplProtocolTests(unittest.TestCase):
    def test_p1_profile_builds_eleph_label_bitmap_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_tspl_p1")
        pixels = [0] * (384 * 8)
        pixels[0] = 1
        raster = RasterBuffer(pixels=pixels, width=384, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=3,
        )

        expected_order = (
            b"SIZE 48 mm,1 mm\r\n",
            b"GAP 2 mm,0 mm\r\n",
            b"DIRECTION 1\r\n",
            b"CLS\r\n",
            b"BITMAP 0,0,48,8,0,\x7f",
            b"PRINT 1,1\r\n",
        )
        positions = [job.payload.index(marker) for marker in expected_order]
        self.assertEqual(positions, sorted(positions))
        self.assertFalse(job.payload.startswith(b"\x10\xff\x10\x03"))
        self.assertNotIn(b"SET RIBBON", job.payload)
        self.assertNotIn(b"REFERENCE", job.payload)
        self.assertNotIn(b"SPEED ", job.payload)
        self.assertNotIn(b"DENSITY ", job.payload)

    def test_eleph_profile_is_ble_only_and_does_not_claim_toprint_media_modes(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_tspl_p1")

        self.assertEqual(device.protocol_family, ProtocolFamily.ELEPH_TSPL)
        self.assertEqual(device.protocol_variant, "p1")
        self.assertEqual(device.image_pipeline.encoding, ImageEncoding.ELEPH_TSPL_BITMAP)
        self.assertFalse(device.profile.use_spp)
        self.assertEqual(PrinterProtocol(device).supported_paper_modes(), (PaperMode.TAG,))
        self.assertEqual(device.profile.stream.chunk_size, 20)
        self.assertEqual(device.profile.stream.delay_ms, 30)

    def test_catalog_detects_eleph_p1_without_stealing_other_p1_profiles(self) -> None:
        catalog = PrinterCatalog.load()

        eleph = catalog.detect_device("P1_F30E")
        self.assertIsNotNone(eleph)
        assert eleph is not None
        self.assertEqual(eleph.profile_key, "eleph_tspl_p1")
        self.assertEqual(eleph.protocol_family, ProtocolFamily.ELEPH_TSPL)

        for name in ("P1", "P1-1234"):
            matches = catalog.detect_model(name)
            self.assertEqual(
                {candidate.model.model_key for candidate in matches},
                {"pocket_printer", "toprint_tspl_p1"},
            )
            self.assertIsNone(catalog.detect_device(name))


if __name__ == "__main__":
    unittest.main()
