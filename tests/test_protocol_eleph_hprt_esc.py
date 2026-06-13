from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class HprtEscProtocolTests(unittest.TestCase):
    def test_zl1_profile_builds_source_ordered_esc_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_hprt_esc_zl1")
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

        self.assertEqual([step.label for step in job.steps], ["hprt-media-type", "hprt-esc-job"])
        self.assertEqual(job.steps[0].data, b"\x10\xff\x10\x03\x02")
        self.assertTrue(job.steps[1].data.startswith(b"\x10\xff\xfe\x01"))
        self.assertEqual(
            job.payload,
            b"".join(
                (
                    b"\x10\xff\x10\x03\x02",
                    b"\x10\xff\xfe\x01",
                    b"\x00" * 12,
                    b"\x1b\x61\x01",
                    b"\x1d\x76\x30\x00\x01\x00\x02\x00\x80\x40",
                    b"\x1d\x0c",
                    b"\x10\xff\xfe\x45",
                    b"\x10\xff\x10\x00\x01",
                )
            ),
        )

    def test_zl1_paper_modes_select_media_type(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_hprt_esc_zl1")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)
        protocol = PrinterProtocol(device)

        plain = protocol.build_job(
            RasterSet.from_single(raster),
            is_text=False,
            paper_mode=PaperMode.PLAIN,
        )
        black_tag = protocol.build_job(
            RasterSet.from_single(raster),
            is_text=False,
            paper_mode=PaperMode.BLACK_TAG,
        )

        self.assertTrue(plain.payload.startswith(b"\x10\xff\x10\x03\x01"))
        self.assertTrue(black_tag.payload.startswith(b"\x10\xff\x10\x03\x03"))

    def test_zl1_esc_image_pads_non_byte_aligned_width(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_hprt_esc_zl1")
        raster = RasterBuffer(
            pixels=[0, 0, 0, 0, 0, 0, 0, 0, 1],
            width=9,
            pixel_format=PixelFormat.BW1,
        )

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertIn(b"\x1d\x76\x30\x00\x02\x00\x01\x00\x00\x80", job.payload)

    def test_catalog_detects_eleph_esc_without_stealing_old_p_series(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("P11_F30E", "P2_F30E", "P3_F30E", "P5_F30E", "YHK_F30E"):
            with self.subTest(name=name):
                detected = catalog.detect_device(name)
                self.assertIsNotNone(detected)
                assert detected is not None
                self.assertEqual(detected.profile_key, "eleph_hprt_esc_zl1")
                self.assertEqual(detected.protocol_family, ProtocolFamily.ELEPH_HPRT_ESC)
                self.assertEqual(detected.protocol_variant, "zl1")
                self.assertEqual(detected.image_pipeline.encoding, ImageEncoding.ELEPH_HPRT_ESC_RASTER)

        old_p2 = catalog.detect_device("P2-1234")
        old_p5 = catalog.detect_device("P5-1234")
        self.assertIsNotNone(old_p2)
        self.assertIsNotNone(old_p5)
        assert old_p2 is not None
        assert old_p5 is not None
        self.assertEqual(old_p2.protocol_family, ProtocolFamily.TINY)
        self.assertEqual(old_p5.protocol_family, ProtocolFamily.TINY)

    def test_zl1_paper_motion_uses_eleph_hprt_esc_motion_commands(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_hprt_esc_zl1")
        protocol = PrinterProtocol(device)

        feed = protocol.build_paper_motion("feed")
        retract = protocol.build_paper_motion("retract")

        self.assertEqual(feed.payload, b"\x1b\x4a\x50")
        self.assertEqual(retract.payload, b"\x10\xff\x81\x50")


if __name__ == "__main__":
    unittest.main()
