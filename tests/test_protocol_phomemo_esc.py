from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class PhomemoEscProtocolTests(unittest.TestCase):
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

        self.assertEqual(device.protocol_family, ProtocolFamily.PHOMEMO_ESC)
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

    def test_m110_profile_builds_m110_style_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m110")
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
                    b"\x1b\x4e\x0d\x05",
                    b"\x1b\x4e\x04\x0a",
                    b"\x1f\x11\x0a",
                    b"\x1d\x76\x30\x00\x01\x00\x02\x00\x80\x40",
                    b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00",
                )
            ),
        )

    def test_m110_paper_modes_select_media_type(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m110")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)

        expected = {
            PaperMode.TAG: b"\x1f\x11\x0a",
            PaperMode.PLAIN: b"\x1f\x11\x0b",
            PaperMode.BLACK_TAG: b"\x1f\x11\x26",
        }
        for paper_mode, media_command in expected.items():
            with self.subTest(paper_mode=paper_mode):
                job = PrinterProtocol(device).build_job(
                    RasterSet.from_single(raster),
                    is_text=False,
                    paper_mode=paper_mode,
                )
                self.assertEqual(job.payload[8:11], media_command)

    def test_m220_profile_uses_wide_m110_style_raster_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m220")
        raster = RasterBuffer(pixels=[0] * 576, width=576, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )

        self.assertEqual(device.profile.print_size, 576)
        self.assertEqual(device.profile.dev_dpi, 203)
        self.assertEqual(device.protocol_variant, "m220")
        self.assertIn(b"\x1d\x76\x30\x00\x48\x00\x01\x00", job.payload)
        self.assertTrue(job.payload.endswith(b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00"))

    def test_m02_detection_does_not_steal_m02s(self) -> None:
        catalog = PrinterCatalog.load()

        m02 = catalog.detect_device("Mr.in_M02")
        m02s = catalog.detect_device("M02S")
        m02s_alias = catalog.detect_device("Mr.in_M02S")

        self.assertIsNotNone(m02)
        self.assertIsNotNone(m02s)
        self.assertIsNotNone(m02s_alias)
        assert m02 is not None
        assert m02s is not None
        assert m02s_alias is not None
        self.assertEqual(m02.profile_key, "phomemo_m02")
        self.assertEqual(m02.protocol_family, ProtocolFamily.PHOMEMO_ESC)
        self.assertEqual(m02.protocol_variant, "m02")
        self.assertEqual(m02.image_pipeline.encoding, ImageEncoding.PHOMEMO_ESC_RASTER)
        self.assertEqual(m02s.profile_key, "phomemo_m02s")
        self.assertEqual(m02s.protocol_variant, "m02s")
        self.assertEqual(m02s_alias.profile_key, "phomemo_m02s")

    def test_m02x_detection_does_not_steal_m02_or_m02_pro(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("M02X", "m02x"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_m02x")
                self.assertEqual(device.protocol_variant, "m02x")

        self.assertIsNone(catalog.detect_device("M02X-ABCD"))
        self.assertIsNone(catalog.detect_device("m02x_abcd"))

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

        for name in ("M02 Pro", "M02PRO", "m02pro"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_m02_pro")
                self.assertEqual(device.protocol_variant, "m02_pro")

        self.assertIsNone(catalog.detect_device("M02 PRO-ABCD"))
        self.assertIsNone(catalog.detect_device("m02pro_abcd"))

        m02 = catalog.detect_device("M02")
        self.assertIsNotNone(m02)
        assert m02 is not None
        self.assertEqual(m02.profile_key, "phomemo_m02")

    def test_t02_detection_does_not_steal_other_02_models(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("T02", "T02E", "Q02E", "C02E"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_t02")
                self.assertEqual(device.protocol_variant, "t02")

        self.assertIsNone(catalog.detect_device("T02-ABCD"))
        self.assertIsNone(catalog.detect_device("T02E-ABCD"))
        self.assertIsNone(catalog.detect_device("C02E_1234"))
        self.assertNotEqual(catalog.detect_device("GT02-ABCD").profile_key, "phomemo_t02")
        self.assertNotEqual(catalog.detect_device("YT02").profile_key, "phomemo_t02")

    def test_m110_detection_maps_m110_m120_and_m220(self) -> None:
        catalog = PrinterCatalog.load()

        for name in ("M110", "M110-ABCD", "M110_abcd", "M120", "M120-ABCD"):
            with self.subTest(name=name):
                device = catalog.detect_device(name)
                self.assertIsNotNone(device)
                assert device is not None
                self.assertEqual(device.profile_key, "phomemo_m110")
                self.assertEqual(device.protocol_variant, "m110")

        m220 = catalog.detect_device("M220-ABCD")
        self.assertIsNotNone(m220)
        assert m220 is not None
        self.assertEqual(m220.profile_key, "phomemo_m220")
        self.assertEqual(m220.protocol_variant, "m220")

    def test_m02_supports_plain_paper_mode_and_motion(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m02")
        protocol = PrinterProtocol(device)

        self.assertEqual(protocol.supported_paper_modes(), (PaperMode.PLAIN,))
        self.assertEqual(protocol.build_paper_motion("feed").payload, b"\x1b\x4a\x50")
        self.assertEqual(protocol.build_paper_motion("retract").payload, b"")

    def test_m110_supports_label_paper_modes(self) -> None:
        device = PrinterCatalog.load().device_from_profile("phomemo_m110")
        protocol = PrinterProtocol(device)

        self.assertEqual(
            protocol.supported_paper_modes(),
            (PaperMode.TAG, PaperMode.PLAIN, PaperMode.BLACK_TAG),
        )


if __name__ == "__main__":
    unittest.main()
