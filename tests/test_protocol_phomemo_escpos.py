from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class PhomemoEscposProtocolTests(unittest.TestCase):
    def test_m02_profile_builds_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02")
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

        self.assertEqual(
            job.payload,
            b"".join(
                (
                    b"\x1b\x40",
                    b"\x1b\x61\x01",
                    b"\x1f\x11\x02\x04",
                    b"\x1d\x76\x30\x00\x01\x00\x02\x00\x80\x40",
                    b"\x1b\x64\x02",
                )
            ),
        )

    def test_m02_raster_blocks_split_after_255_lines(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02")
        raster = RasterBuffer(pixels=[0] * (8 * 256), width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertEqual(job.payload.count(b"\x1d\x76\x30\x00\x01\x00\xff\x00"), 1)
        self.assertEqual(job.payload.count(b"\x1d\x76\x30\x00\x01\x00\x01\x00"), 1)

    def test_t02_profile_builds_same_family_with_t02_feed(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_t02")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertEqual(device.protocol_family, ProtocolFamily.PHOMEMO_ESCPOS)
        self.assertEqual(device.protocol_variant, "t02")
        self.assertIn(b"\x1d\x76\x30\x00\x01\x00\x01\x00\x00", job.payload)
        self.assertTrue(job.payload.endswith(b"\x1b\x64\x04"))

    def test_m02_pro_profile_builds_300dpi_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02_pro")
        raster = RasterBuffer(pixels=[0] * 624, width=624, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertEqual(device.profile.print_size, 624)
        self.assertEqual(device.profile.dev_dpi, 300)
        self.assertEqual(device.protocol_variant, "m02_pro")
        self.assertIn(b"\x1d\x76\x30\x00\x4e\x00\x01\x00", job.payload)
        self.assertTrue(job.payload.endswith(b"\x1b\x64\x02"))

    def test_m02x_profile_builds_m02_family_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02x")
        raster = RasterBuffer(pixels=[0] * 384, width=384, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertEqual(device.profile.print_size, 384)
        self.assertEqual(device.profile.dev_dpi, 203)
        self.assertEqual(device.protocol_variant, "m02x")
        self.assertIn(b"\x1d\x76\x30\x00\x30\x00\x01\x00", job.payload)
        self.assertTrue(job.payload.endswith(b"\x1b\x64\x02"))

    def test_m02_detection_does_not_steal_m02s(self) -> None:
        catalog = PrinterCatalog.load()

        m02 = catalog.detect_device("Mr.in_M02")
        m02s = catalog.detect_device("M02S-ABCD")
        m02s_alias = catalog.detect_device("Mr.in_M02S")

        self.assertIsNotNone(m02)
        self.assertIsNotNone(m02s)
        self.assertIsNotNone(m02s_alias)
        assert m02 is not None
        assert m02s is not None
        assert m02s_alias is not None
        self.assertEqual(m02.profile_key, "phomemo_m02")
        self.assertEqual(m02.protocol_family, ProtocolFamily.PHOMEMO_ESCPOS)
        self.assertEqual(m02.protocol_variant, "m02")
        self.assertEqual(m02.image_pipeline.encoding, ImageEncoding.PHOMEMO_ESCPOS_RASTER)
        self.assertEqual(m02s.profile_key, "phomemo_m02s")
        self.assertEqual(m02s.protocol_variant, "m02s")
        self.assertEqual(m02s_alias.profile_key, "phomemo_m02s")

    def test_m02x_detection_does_not_steal_m02_or_m02_pro(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("M02X", "M02X-ABCD", "m02x_abcd"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_m02x")
                self.assertEqual(device.protocol_variant, "m02x")

        m02 = catalog.detect_device("M02")
        m02_pro = catalog.detect_device("M02 Pro")
        self.assertIsNotNone(m02)
        self.assertIsNotNone(m02_pro)
        assert m02 is not None
        assert m02_pro is not None
        self.assertEqual(m02.profile_key, "phomemo_m02")
        self.assertEqual(m02_pro.profile_key, "phomemo_m02_pro")

    def test_m02_pro_detection_does_not_steal_m02(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("M02 Pro", "M02PRO", "M02 PRO-ABCD", "m02pro_abcd"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_m02_pro")
                self.assertEqual(device.protocol_variant, "m02_pro")

        m02 = catalog.detect_device("M02")
        self.assertIsNotNone(m02)
        assert m02 is not None
        self.assertEqual(m02.profile_key, "phomemo_m02")

    def test_t02_detection_does_not_steal_other_02_models(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("T02", "T02-ABCD", "T02E-ABCD", "Q02E", "C02E_1234"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_t02")
                self.assertEqual(device.protocol_variant, "t02")

        self.assertNotEqual(catalog.detect_device("GT02-ABCD").profile_key, "phomemo_t02")
        self.assertNotEqual(catalog.detect_device("YT02").profile_key, "phomemo_t02")

    def test_m02_supports_plain_paper_mode_and_motion(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02")
        protocol = PrinterProtocol(device)

        self.assertEqual(protocol.supported_paper_modes(), (PaperMode.PLAIN,))
        self.assertEqual(protocol.build_paper_motion("feed").payload, b"\x1b\x4a\x50")
        self.assertEqual(protocol.build_paper_motion("retract").payload, b"")


if __name__ == "__main__":
    unittest.main()
