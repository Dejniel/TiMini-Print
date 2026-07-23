from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PageFlow, PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class InstaPrintCoreProtocolTests(unittest.TestCase):
    def test_ctp500_profile_builds_coreprint_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_model("instaprint_ctp500_coreprint")
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

        self.assertEqual(device.protocol_family, ProtocolFamily.INSTAPRINT_CORE)
        self.assertEqual(device.protocol_variant, "ctp500")
        self.assertEqual(device.image_pipeline.encoding, ImageEncoding.INSTAPRINT_CORE_RASTER)
        self.assertEqual(
            job.payload,
            b"".join(
                (
                    b"\x1b\x40",
                    b"\x1d\x49\xf0\x0f",
                    b"\x1d\x76\x30\x00\x01\x00\x02\x00\x80\x40",
                    b"\x0a\x0a\x0a\x0a",
                )
            ),
        )

    def test_ctp500_density_follows_profile_blackening(self) -> None:
        device = PrinterCatalog.load().device_from_model("instaprint_ctp500_coreprint")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)

        low = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=1,
        )
        high = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=5,
        )

        self.assertTrue(low.payload.startswith(b"\x1b\x40\x1d\x49\xf0\x0c"))
        self.assertTrue(high.payload.startswith(b"\x1b\x40\x1d\x49\xf0\x12"))

    def test_ctp500_continuous_chunk_omits_intermediate_feed(self) -> None:
        device = PrinterCatalog.load().device_from_model("instaprint_ctp500_coreprint")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=True,
            page_index=1,
            page_count=2,
            page_flow=PageFlow.CONTINUOUS,
        )

        self.assertFalse(job.payload.endswith(b"\x0a" * 4))

    def test_ctp500_supports_plain_paper_only(self) -> None:
        device = PrinterCatalog.load().device_from_model("instaprint_ctp500_coreprint")

        self.assertEqual(PrinterProtocol(device).supported_paper_modes(), (PaperMode.PLAIN,))

    def test_ctp500_manual_feed_uses_coreprint_feed_sequence(self) -> None:
        device = PrinterCatalog.load().device_from_model("instaprint_ctp500_coreprint")

        self.assertEqual(PrinterProtocol(device).build_paper_motion("feed").payload, b"\x0a" * 4)
        self.assertEqual(PrinterProtocol(device).build_paper_motion("retract").payload, b"")
