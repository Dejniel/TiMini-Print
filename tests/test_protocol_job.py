from __future__ import annotations

import importlib
import unittest
import zlib
from unittest.mock import patch

from tests.helpers import install_crc8_stub
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol, ProtocolReplyExpectation, ProtocolStepOperation
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5g import encode_density_payload
from timiniprint.protocol.families.v5x import V5X_FINALIZE_PACKET, V5X_GET_SERIAL_PACKET


class ProtocolJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.commands = importlib.import_module("timiniprint.protocol.commands")
        cls.builders = importlib.import_module("timiniprint.protocol._builders")
        cls.types = importlib.import_module("timiniprint.protocol.types")
        cls.raster = importlib.import_module("timiniprint.raster")
        cls.tiny_raw = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1,),
            encoding=cls.types.ImageEncoding.TINY_RAW,
        )
        cls.tiny_rle = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1,),
            encoding=cls.types.ImageEncoding.TINY_RLE,
        )
        cls.luck_normal_raw = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1, cls.raster.PixelFormat.GRAY4, cls.raster.PixelFormat.GRAY8),
            encoding=cls.types.ImageEncoding.LUCK_NORMAL_RAW,
        )
        cls.luck_normal_gray = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.GRAY4, cls.raster.PixelFormat.GRAY8, cls.raster.PixelFormat.BW1),
            encoding=cls.types.ImageEncoding.LUCK_NORMAL_GRAY,
        )
        cls.luck_normal_compressed = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1,),
            encoding=cls.types.ImageEncoding.LUCK_NORMAL_COMPRESSED,
        )
        cls.v5x_dot = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.BW1,
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.GRAY8,
            ),
            encoding=cls.types.ImageEncoding.V5X_DOT,
        )
        cls.v5x_gray = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.GRAY8,
                cls.raster.PixelFormat.BW1,
            ),
            encoding=cls.types.ImageEncoding.V5X_GRAY,
        )
        cls.v5c_a4 = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1,),
            encoding=cls.types.ImageEncoding.V5C_A4,
        )
        cls.v5c_a5_gray4 = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.GRAY8,
                cls.raster.PixelFormat.BW1,
            ),
            encoding=cls.types.ImageEncoding.V5C_A5,
        )
        cls.v5c_a5_gray8 = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.GRAY8,
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.BW1,
            ),
            encoding=cls.types.ImageEncoding.V5C_A5,
        )
        cls.dck = cls.types.ImagePipelineConfig(
            formats=(cls.raster.PixelFormat.BW1,),
            encoding=cls.types.ImageEncoding.DCK_DEFAULT,
        )
        cls.v5g_dot = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.BW1,
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.GRAY8,
            ),
            encoding=cls.types.ImageEncoding.V5G_DOT,
        )
        cls.v5g_gray = cls.types.ImagePipelineConfig(
            formats=(
                cls.raster.PixelFormat.GRAY4,
                cls.raster.PixelFormat.GRAY8,
                cls.raster.PixelFormat.BW1,
            ),
            encoding=cls.types.ImageEncoding.V5G_GRAY,
        )

    def _bw_raster(self, pixels: list[int], width: int = 8):
        return self.raster.RasterBuffer(
            pixels=pixels,
            width=width,
            pixel_format=self.raster.PixelFormat.BW1,
        )

    def _raster_set(self, *rasters):
        return self.raster.RasterSet(rasters={raster.pixel_format: raster for raster in rasters})

    def test_build_print_payload_contains_expected_sections(self) -> None:
        payload = self.builders._build_print_payload(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY,
            image_pipeline=self.tiny_raw,
        )
        self.assertIn(bytes([0xAF]), payload)
        self.assertIn(bytes([0xBE]), payload)
        self.assertIn(bytes([0xBD]), payload)
        self.assertIn(bytes([0xA2]), payload)

    def test_build_job_appends_final_sequence(self) -> None:
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
            image_pipeline=self.tiny_raw,
        )
        self.assertGreaterEqual(data.count(bytes([0xA1])), 2)
        self.assertIn(bytes([0xA3]), data)

    def test_build_tiny_job_requires_speed(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires speed defaults"):
            self.builders._build_job(
                pixels=[1, 0, 1, 0, 1, 0, 1, 0],
                width=8,
                is_text=False,
                speed=None,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.TINY,
                feed_padding=12,
                dev_dpi=203,
                image_pipeline=self.tiny_raw,
            )

    def test_line_eight_variant_uses_size8_tail_and_padding(self) -> None:
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
            protocol_variant="line_eight",
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=2,
            left_padding_pixels=8,
            image_pipeline=self.tiny_raw,
        )

        self.assertIn(bytes([0x51, 0x78, 0xA2, 0x00, 0x02, 0x00]), data)
        self.assertIn(bytes([0x51, 0x78, 0xA1, 0x00, 0x03, 0x00, 0x90, 0x00, 0x11]), data)
        self.assertNotIn(bytes([0xA1, 0x00, 0x02, 0x00, 0x30, 0x00]), data)

    def test_line_eight_a4_sheet_uses_max_height_minus_raster_height(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0] * 2,
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY,
            protocol_variant="line_eight",
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=2,
            a4_sheet_max_height=20,
            image_pipeline=self.tiny_raw,
            paper_mode=self.types.PaperMode.A4_SHEET,
        )

        self.assertIn(bytes([0x51, 0x78, 0xA1, 0x00, 0x03, 0x00, 0x12, 0x00, 0x11]), data)

    def test_line_eight_a4xii_a4_sheet_uses_fixed_feed(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=False,
            protocol_family=ProtocolFamily.TINY,
            protocol_variant="line_eight",
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=2,
            a4xii=True,
            image_pipeline=self.tiny_raw,
            paper_mode=self.types.PaperMode.A4_SHEET,
        )

        self.assertIn(bytes([0x51, 0x78, 0xA1, 0x00, 0x03, 0x00, 0xF4, 0x01, 0x11]), data)

    def test_professional_variant_uses_raw_fallback_flow(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0] * 201,
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY,
            protocol_variant="professional",
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=2,
            left_padding_pixels=8,
            image_pipeline=self.tiny_raw,
        )

        stop_print = bytes([0x51, 0x78, 0xA6, 0x00, 0x01, 0x00, 0x05, 0x1B, 0xFF])
        speed_10 = bytes([0x51, 0x78, 0xBD, 0x00, 0x01, 0x00, 0x0A, 0x36, 0xFF])
        self.assertTrue(data.startswith(stop_print))
        self.assertEqual(data.count(speed_10), 1)

    def test_esc_star_variant_uses_esc_star_flow(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=8,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY_PREFIXED,
            protocol_variant="esc_star",
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.tiny_raw,
        )

        self.assertTrue(data.startswith(bytes([0x1B, 0x40, 0x12, 0x23, 0x08])))
        self.assertIn(bytes([0x12, 0x51, 0x78, 0xBE, 0x00, 0x01, 0x00, 0x00]), data)
        self.assertIn(bytes([0x1B, 0x2A, 0x21, 0x08, 0x00]), data)
        self.assertIn(bytes([0x1B, 0x4A, 0x00, 0x0A]), data)
        self.assertTrue(data.endswith(bytes([0x00, 0xFF])))
        self.assertIn(bytes([0x1B, 0x64, 0x03, 0x12, 0x51, 0x78, 0xA3]), data)

    def test_esc_star_eight_variant_uses_profile_one_length(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=8,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY_PREFIXED,
            protocol_variant="esc_star_eight",
            feed_padding=12,
            dev_dpi=203,
            one_length=8,
            image_pipeline=self.tiny_raw,
        )

        self.assertIn(bytes([0x1B, 0x64, 0x08, 0x12, 0x51, 0x78, 0xA3]), data)

    def test_esc_star_eight_a4_sheet_uses_max_height_bands(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0] * 24,
            width=8,
            is_text=False,
            speed=10,
            energy=8,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.TINY_PREFIXED,
            protocol_variant="esc_star_eight",
            feed_padding=12,
            dev_dpi=203,
            one_length=8,
            a4_sheet_max_height=72,
            image_pipeline=self.tiny_raw,
            paper_mode=self.types.PaperMode.A4_SHEET,
        )

        self.assertIn(bytes([0x1B, 0x64, 0x02, 0x12, 0x51, 0x78, 0xA3]), data)

    def test_build_from_raster_validates(self) -> None:
        raster = self.raster.RasterBuffer(pixels=[1, 0, 1], width=2)
        with self.assertRaisesRegex(ValueError, "multiple of width"):
            self.builders._build_job_from_raster(
                raster=raster,
                is_text=False,
                speed=10,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.TINY,
                feed_padding=12,
                dev_dpi=203,
                image_pipeline=self.tiny_raw,
            )

    def test_build_luck_normal_job_uses_raw_bitmap_recipe(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_job_allows_missing_speed(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=None,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_a4_job_sets_plain_paper_mode(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x10])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x90])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_ppa2l_profile_default_builds_tag_paper_mode(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        raster_set = self._raster_set(self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]))

        job = PrinterProtocol(device).build_job(raster_set, is_text=False)

        self.assertTrue(job.wait_for_completion)
        self.assertFalse(hasattr(job, "runtime_controller"))
        self.assertIn(bytes([0x1F, 0x80, 0x01, 0x20]), job.payload)
        self.assertIn(bytes([0x10, 0xFF, 0xF1, 0x45]), job.payload)
        self.assertEqual([step.label for step in job.steps[:5]], ["density", "status", "enable", "wakeup", "paper type"])
        self.assertEqual(job.steps[0].operation, ProtocolStepOperation.QUERY)
        self.assertEqual(job.steps[0].expect, ProtocolReplyExpectation.OK)
        self.assertEqual(job.steps[1].operation, ProtocolStepOperation.QUERY)
        self.assertFalse(job.steps[1].include_in_payload)
        self.assertEqual(job.steps[-1].label, "finalize")
        self.assertEqual(job.steps[-1].operation, ProtocolStepOperation.QUERY)
        self.assertEqual(job.steps[-1].expect, ProtocolReplyExpectation.OK_OR_AA)
        self.assertNotIn(bytes([0x10, 0xFF, 0x40]), job.payload)

    def test_ppa2l_plain_override_omits_tag_paper_mode(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        raster_set = self._raster_set(self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]))

        job = PrinterProtocol(device).build_job(
            raster_set,
            is_text=False,
            paper_mode=self.types.PaperMode.PLAIN,
        )

        self.assertNotIn(bytes([0x1F, 0x80, 0x01, 0x20]), job.payload)

    def test_ppa2l_rejects_paper_mode_without_profile_preset(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        raster_set = self._raster_set(self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]))

        with self.assertRaisesRegex(ValueError, "profile luck_ppa2l does not define paper mode tattoo"):
            PrinterProtocol(device).build_job(
                raster_set,
                is_text=False,
                paper_mode=self.types.PaperMode.TATTOO,
            )

    def test_build_luck_normal_gray_job_uses_gray_bitmap_header(self) -> None:
        raster_set = self._raster_set(
            self._bw_raster([1, 0], width=2),
            self.raster.RasterBuffer(
                pixels=[15, 0],
                width=2,
                pixel_format=self.raster.PixelFormat.GRAY4,
            ),
        )
        data = self.builders._build_job_from_raster_set(
            raster_set=raster_set,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_gray,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x47, 0x59, 0x10, 0x01, 0x00, 0x01, 0x00, 0xF0])
            + bytes([0x1B, 0x4A, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_h_gray_job_uses_runtime_gray_level_override(self) -> None:
        from timiniprint.protocol import RuntimePrintCapabilities

        raster_set = self._raster_set(
            self._bw_raster([1, 1], width=2),
            self.raster.RasterBuffer(
                pixels=[15, 15],
                width=2,
                pixel_format=self.raster.PixelFormat.GRAY4,
            ),
        )
        data = self.builders._build_job_from_raster_set(
            raster_set=raster_set,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="lujiang_normal_h",
            feed_padding=12,
            dev_dpi=300,
            image_pipeline=self.luck_normal_gray,
            runtime_capabilities=RuntimePrintCapabilities(
                supports_gray=True,
                gray_level_override=12,
            ),
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x47, 0x59, 0x0C, 0x01, 0x00, 0x01, 0x00, 0xBB])
            + bytes([0x1B, 0x4A, 0x3C])
            + bytes([0x1B, 0xBB, 0xBB])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_tag_job_sets_tag_mode_and_positions(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TAG,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x20])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1D, 0x0C])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_lujiang_normal_plain_job_marks_last_page_only(self) -> None:
        first_page = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="lujiang_normal",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            page_index=1,
            page_count=2,
        )
        last_page = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="lujiang_normal",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            page_index=2,
            page_count=2,
        )
        self.assertNotIn(bytes([0x1B, 0xBB, 0xBB]), first_page)
        self.assertIn(bytes([0x1B, 0xBB, 0xBB]), last_page)
        self.assertTrue(last_page.endswith(bytes([0x1B, 0xBB, 0xBB, 0x10, 0xFF, 0xF1, 0x45])))

    def test_build_lujiang_normal_tag_job_positions_and_marks_last_page(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="lujiang_normal",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TAG,
            page_index=1,
            page_count=1,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x20])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1D, 0x0C])
            + bytes([0x1B, 0xBB, 0xBB])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_a4_folder_job_sets_folder_mode_and_positions(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.FOLDER,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x30])
            + bytes([0x1F, 0x11, 0x51])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1D, 0x0C])
            + bytes([0x1F, 0x11, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_a4_tag_job_adjusts_first_and_last_page(self) -> None:
        first_page = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TAG,
            page_index=1,
            page_count=2,
        )
        last_page = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TAG,
            page_index=2,
            page_count=2,
        )
        self.assertIn(bytes([0x1F, 0x11, 0x51]), first_page)
        self.assertNotIn(bytes([0x1F, 0x11, 0x50]), first_page)
        self.assertNotIn(bytes([0x1F, 0x11, 0x51]), last_page)
        self.assertIn(bytes([0x1F, 0x11, 0x50]), last_page)

    def test_build_luck_normal_a4_tattoo_job_uses_plain_paper_type(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TATTOO,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x10])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x90])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_qirui_q1_job_uses_enable_mode_2(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="qirui_q1",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x02])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_qirui_q2_job_uses_custom_300dpi_end_line_dot(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant="qirui_q2",
            feed_padding=12,
            dev_dpi=300,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x02])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x82])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_qirui_variant_rejects_tattoo_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not support paper mode tattoo"):
            self.builders._build_job(
                pixels=[1, 0, 1, 0, 1, 0, 1, 0],
                width=8,
                is_text=False,
                speed=10,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.LUCK_NORMAL,
                protocol_variant="qirui_q1",
                feed_padding=12,
                dev_dpi=200,
                image_pipeline=self.luck_normal_raw,
                paper_mode=self.types.PaperMode.TATTOO,
            )

    def test_build_luckp_a41_plain_job_omits_paper_type_command(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="luckp_a41",
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x90])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luckp_a41_folder_job_positions_without_paper_type(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="luckp_a41",
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.FOLDER,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1D, 0x0C])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luckp_a41_tag_job_keeps_a4_adjust_markers(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="luckp_a41",
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TAG,
            page_index=1,
            page_count=1,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x20])
            + bytes([0x1F, 0x11, 0x51])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1D, 0x0C])
            + bytes([0x1F, 0x11, 0x50])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_a4_tattoo_64_variant_uses_tattoo_paper_type(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="a4_tattoo_64",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TATTOO,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x40])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x90])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_a4_tattoo_64_endline96_variant_uses_short_feed(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="a4_tattoo_64_endline96",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TATTOO,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x40])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x60])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_apl86_plain_job_sets_paper_type_before_enable(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="apl86",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x1F, 0x80, 0x01, 0x10])
            + bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x90])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_d80_tattoo_job_is_rejected_until_runtime_config_is_modeled(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not support paper mode tattoo"):
            self.builders._build_job(
                pixels=[1, 0, 1, 0, 1, 0, 1, 0],
                width=8,
                is_text=False,
                speed=20,
                energy=10000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
                protocol_variant="d80",
                feed_padding=12,
                dev_dpi=200,
                image_pipeline=self.luck_normal_raw,
                paper_mode=self.types.PaperMode.TATTOO,
            )

    def test_build_d80h_tattoo_job_uses_tattoo_paper_type(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="d80h",
            feed_padding=12,
            dev_dpi=300,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TATTOO,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x40])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0xD8])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_luck_normal_a4_supported_paper_modes_can_vary_by_variant(self) -> None:
        families = importlib.import_module("timiniprint.protocol.families.luck.normal_a4")

        d80_modes = families.BEHAVIOR.supported_paper_modes_resolver("d80")
        d80h_modes = families.BEHAVIOR.supported_paper_modes_resolver("d80h")

        self.assertNotIn(self.types.PaperMode.TATTOO, d80_modes)
        self.assertIn(self.types.PaperMode.TATTOO, d80h_modes)

    def test_luck_normal_supported_paper_modes_can_vary_by_variant(self) -> None:
        families = importlib.import_module("timiniprint.protocol.families.luck.normal")

        default_modes = families.BEHAVIOR.supported_paper_modes
        lujiang_modes = families.BEHAVIOR.supported_paper_modes_resolver("lujiang_normal")

        self.assertEqual(
            default_modes,
            (self.types.PaperMode.PLAIN, self.types.PaperMode.TAG),
        )
        self.assertEqual(
            lujiang_modes,
            (self.types.PaperMode.PLAIN, self.types.PaperMode.TAG),
        )
        self.assertNotIn(self.types.PaperMode.TATTOO, lujiang_modes)

    def test_build_lujiang_a4_tattoo_job_uses_tattoo_paper_type_and_short_feed(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="lujiang_a4",
            feed_padding=12,
            dev_dpi=200,
            image_pipeline=self.luck_normal_raw,
            paper_mode=self.types.PaperMode.TATTOO,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x40])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x60])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_printer_protocol_can_downgrade_luck_gray_pipeline_after_runtime_probe(self) -> None:
        from timiniprint.devices import PrinterCatalog
        from timiniprint.protocol import RuntimePrintCapabilities
        from timiniprint.protocol import PrinterProtocol

        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")
        protocol = PrinterProtocol(device)

        resolved = protocol.resolve_image_pipeline(
            image_pipeline=self.luck_normal_gray,
            runtime_capabilities=RuntimePrintCapabilities(supports_gray=False),
        )

        self.assertEqual(resolved.encoding, self.types.ImageEncoding.LUCK_NORMAL_RAW)
        self.assertEqual(resolved.default_format, self.raster.PixelFormat.BW1)

    def test_build_a49h_plain_job_uses_custom_300dpi_end_line_dot(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=20,
            energy=10000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant="a49h",
            feed_padding=12,
            dev_dpi=300,
            image_pipeline=self.luck_normal_raw,
        )
        self.assertEqual(
            data,
            bytes([0x10, 0xFF, 0xF1, 0x03])
            + bytes(12)
            + bytes([0x1F, 0x80, 0x01, 0x10])
            + bytes([0x1D, 0x76, 0x30, 0x00, 0x01, 0x00, 0x01, 0x00, 0xAA])
            + bytes([0x1B, 0x4A, 0x60])
            + bytes([0x10, 0xFF, 0xF1, 0x45]),
        )

    def test_build_luck_normal_compressed_job_uses_us_dle_and_zlib_wbits_10(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            feed_padding=12,
            dev_dpi=203,
            image_pipeline=self.luck_normal_compressed,
        )
        prefix = bytes([0x10, 0xFF, 0xF1, 0x03]) + bytes(12)
        suffix = bytes([0x1B, 0x4A, 0x50]) + bytes([0x10, 0xFF, 0xF1, 0x45])
        self.assertTrue(data.startswith(prefix))
        self.assertTrue(data.endswith(suffix))

        compressed_bitmap = data[len(prefix) : -len(suffix)]
        self.assertEqual(
            compressed_bitmap[:6],
            bytes([0x1F, 0x10, 0x00, 0x01, 0x00, 0x01]),
        )
        body_length = int.from_bytes(compressed_bitmap[6:10], "big")
        compressed_body = compressed_bitmap[10:]
        self.assertEqual(body_length, len(compressed_body))
        self.assertEqual(compressed_body[:2], bytes([0x28, 0x91]))
        self.assertEqual(zlib.decompress(compressed_body, wbits=10), bytes([0xAA]))

    def test_tiny_rejects_paper_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not support paper mode tag"):
            self.builders._build_job(
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
                image_pipeline=self.tiny_raw,
                paper_mode=self.types.PaperMode.TAG,
            )

    def test_build_v5x_job_uses_family_specific_sequence(self) -> None:
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
            image_pipeline=self.v5x_dot,
        )
        self.assertTrue(data.startswith(V5X_GET_SERIAL_PACKET))
        self.assertIn(
            self.commands.make_packet(0xA2, bytes([0x5D]), ProtocolFamily.V5X),
            data,
        )
        self.assertIn(
            self.commands.make_packet(
                0xA9,
                bytes.fromhex("010030010000"),
                ProtocolFamily.V5X,
            ),
            data,
        )
        self.assertIn(bytes([0x55]), data)
        self.assertTrue(data.endswith(V5X_FINALIZE_PACKET))

    def test_build_v5x_job_uses_standard_mode_when_labels_disabled(self) -> None:
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
            can_print_label=False,
            image_pipeline=self.v5x_dot,
        )
        self.assertIn(
            self.commands.make_packet(
                0xA9,
                bytes.fromhex("010030000000"),
                ProtocolFamily.V5X,
            ),
            data,
        )

    def test_build_v5x_gray_job_uses_gray4_payload(self) -> None:
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]),
            self.raster.RasterBuffer(
                pixels=[15, 14, 13, 12, 11, 10, 9, 8],
                width=8,
                pixel_format=self.raster.PixelFormat.GRAY4,
            ),
        )
        data = self.builders._build_job_from_raster_set(
            raster_set=raster_set,
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
            image_pipeline=self.v5x_gray,
        )

        height_bytes = bytes([0x01, 0x00])
        expected_start = (
            ProtocolFamily.V5X.packet_prefix
            + bytes([0xA9, 0x00, 0x02, 0x00])
            + height_bytes
            + bytes([self.commands.crc8_value(height_bytes), 0xFF])
        )
        self.assertTrue(data.startswith(V5X_GET_SERIAL_PACKET))
        self.assertIn(
            self.commands.make_packet(0xA2, bytes([0x55]), ProtocolFamily.V5X),
            data,
        )
        self.assertIn(expected_start, data)
        self.assertIn(bytes.fromhex("FEDCBA98"), data)
        self.assertTrue(data.endswith(V5X_FINALIZE_PACKET))

    def test_build_v5x_gray_job_supports_gray8_raster(self) -> None:
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]),
            self.raster.RasterBuffer(
                pixels=[0, 16, 32, 48, 64, 80, 96, 112],
                width=8,
                pixel_format=self.raster.PixelFormat.GRAY8,
            ),
        )
        data = self.builders._build_job_from_raster_set(
            raster_set=raster_set,
            is_text=False,
            speed=10,
            energy=5000,
            density=None,
            blackening=2,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5X,
            feed_padding=12,
            dev_dpi=203,
            can_print_label=False,
            image_pipeline=self.v5x_gray.with_default_format(self.raster.PixelFormat.GRAY8),
        )

        self.assertIn(
            self.commands.make_packet(0xA2, bytes([0x50]), ProtocolFamily.V5X),
            data,
        )
        self.assertIn(bytes([0, 16, 32, 48, 64, 80, 96, 112]), data)

    def test_build_v5c_job_uses_family_specific_sequence(self) -> None:
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
            image_pipeline=self.v5c_a4,
        )
        self.assertTrue(
            data.startswith(
                self.commands.make_packet(0xA2, bytes([0x03, 0x01]), ProtocolFamily.V5C)
            )
        )
        self.assertIn(self.commands.make_packet(0xA3, bytes([0x01]), ProtocolFamily.V5C), data)
        self.assertIn(self.commands.make_packet(0xA4, bytes([0x55]), ProtocolFamily.V5C), data)
        self.assertNotIn(bytes([0x1D, 0x76, 0x30, 0x00]), data)
        self.assertIn(self.commands.make_packet(0xA6, bytes([0x30, 0x00]), ProtocolFamily.V5C), data)
        self.assertTrue(
            data.endswith(self.commands.make_packet(0xA1, bytes([0x00]), ProtocolFamily.V5C))
        )

    def test_build_v5c_compressed_job_uses_a5_frames(self) -> None:
        gray_raster = self.raster.RasterBuffer(
            pixels=[15, 14, 13, 12, 11, 10, 9, 8, 15, 14, 13, 12, 11, 10, 9, 8],
            width=8,
            pixel_format=self.raster.PixelFormat.GRAY4,
        )
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0] * 2),
            gray_raster,
        )
        captured_blocks = []
        with patch(
            "timiniprint.protocol.families.v5c.compress_lzo1x_1",
            side_effect=lambda data: captured_blocks.append(data) or bytes.fromhex("AABBCC"),
        ):
            data = self.builders._build_job_from_raster_set(
                raster_set=raster_set,
                is_text=False,
                speed=10,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.V5C,
                feed_padding=12,
                dev_dpi=203,
                image_pipeline=self.v5c_a5_gray4,
            )

        self.assertEqual(captured_blocks, [bytes.fromhex("FEDCBA98FEDCBA98")])
        expected_payload = (8).to_bytes(2, "little") + (3).to_bytes(2, "little") + bytes.fromhex("AABBCC")
        self.assertIn(
            self.commands.make_packet(0xA5, expected_payload, ProtocolFamily.V5C),
            data,
        )
        self.assertNotIn(
            self.commands.make_packet(0xA4, bytes([0x55]), ProtocolFamily.V5C),
            data,
        )

    def test_build_v5c_compressed_job_supports_gray8_raster(self) -> None:
        gray_raster = self.raster.RasterBuffer(
            pixels=[0, 16, 32, 48, 64, 80, 96, 112, 1, 17, 33, 49, 65, 81, 97, 113],
            width=8,
            pixel_format=self.raster.PixelFormat.GRAY8,
        )
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0] * 2),
            gray_raster,
        )
        captured_blocks = []
        with patch(
            "timiniprint.protocol.families.v5c.compress_lzo1x_1",
            side_effect=lambda data: captured_blocks.append(data) or bytes.fromhex("AABBCC"),
        ):
            data = self.builders._build_job_from_raster_set(
                raster_set=raster_set,
                is_text=False,
                speed=10,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.V5C,
                feed_padding=12,
                dev_dpi=203,
                image_pipeline=self.v5c_a5_gray8,
            )

        self.assertEqual(captured_blocks, [bytes(gray_raster.pixels)])
        expected_payload = (16).to_bytes(2, "little") + (3).to_bytes(2, "little") + bytes.fromhex("AABBCC")
        self.assertIn(
            self.commands.make_packet(0xA5, expected_payload, ProtocolFamily.V5C),
            data,
        )

    def test_build_v5c_compressed_job_raises_when_compressor_fails(self) -> None:
        gray_raster = self.raster.RasterBuffer(
            pixels=[15, 13, 11, 9, 7, 5, 3, 1],
            width=8,
            pixel_format=self.raster.PixelFormat.GRAY4,
        )
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0]),
            gray_raster,
        )
        with patch(
            "timiniprint.protocol.families.v5c.compress_lzo1x_1",
            side_effect=RuntimeError("python-lzo is required for V5C compressed jobs"),
        ):
            with self.assertRaisesRegex(RuntimeError, "python-lzo is required"):
                self.builders._build_job_from_raster_set(
                    raster_set=raster_set,
                    is_text=False,
                    speed=10,
                    energy=5000,
                    density=None,
                    blackening=3,
                    lsb_first=True,
                    protocol_family=ProtocolFamily.V5C,
                    feed_padding=12,
                    dev_dpi=203,
                    image_pipeline=self.v5c_a5_gray4,
                )

    def test_build_dck_job_is_not_implemented(self) -> None:
            with self.assertRaisesRegex(NotImplementedError, "DCK protocol family"):
                self.builders._build_job(
                    pixels=[1, 0, 1, 0, 1, 0, 1, 0],
                    width=8,
                    is_text=False,
                speed=10,
                energy=5000,
                density=None,
                blackening=3,
                lsb_first=True,
                protocol_family=ProtocolFamily.DCK,
                feed_padding=12,
                    dev_dpi=203,
                    image_pipeline=self.dck,
                )

    def test_build_v5g_dot_job_uses_v5g_sequence(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=None,
            energy=15000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5G,
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=1,
            image_pipeline=self.v5g_dot,
        )

        self.assertIn(
            self.commands.make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G),
            data,
        )
        self.assertIn(
            self.commands.make_packet(0xA6, bytes.fromhex("AA551738445F5F5F44382C"), ProtocolFamily.V5G),
            data,
        )
        self.assertIn(
            self.commands.make_packet(0xAF, (15000).to_bytes(2, "little"), ProtocolFamily.V5G),
            data,
        )
        self.assertIn(
            self.commands.make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G),
            data,
        )
        self.assertIn(
            self.commands.make_packet(0xBD, bytes([0x0A]), ProtocolFamily.V5G),
            data,
        )
        self.assertIn(
            self.commands.make_packet(0xBD, bytes([0x19]), ProtocolFamily.V5G),
            data,
        )
        self.assertEqual(
            data.count(self.commands.make_packet(0xBD, bytes([0x0A]), ProtocolFamily.V5G)),
            1,
        )
        self.assertEqual(
            data.count(self.commands.make_packet(0xBD, bytes([0x19]), ProtocolFamily.V5G)),
            1,
        )
        self.assertIn(
            self.commands.make_packet(0xA2, bytes([0x55]), ProtocolFamily.V5G),
            data,
        )
        self.assertLess(
            data.index(self.commands.make_packet(0xA2, bytes([0x55]), ProtocolFamily.V5G)),
            data.rindex(self.commands.make_packet(0xBD, bytes([0x19]), ProtocolFamily.V5G)),
        )
        self.assertEqual(
            data.count(self.commands.make_packet(0xA1, bytes([0x30, 0x00]), ProtocolFamily.V5G)),
            1,
        )
        state_query = self.commands.make_packet(0xA3, bytes([0x00]), ProtocolFamily.V5G)
        quality = self.commands.make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
        energy = self.commands.make_packet(0xAF, (15000).to_bytes(2, "little"), ProtocolFamily.V5G)
        start_lattice = self.commands.make_packet(
            0xA6,
            bytes.fromhex("AA551738445F5F5F44382C"),
            ProtocolFamily.V5G,
        )
        print_mode = self.commands.make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
        self.assertLess(data.index(state_query), data.index(quality))
        self.assertLess(data.index(quality), data.index(start_lattice))
        self.assertLess(data.index(start_lattice), data.index(energy))
        self.assertLess(data.index(energy), data.index(print_mode))
        self.assertEqual(data.count(state_query), 3)
        self.assertTrue(data.endswith(self.commands.make_packet(0xA3, bytes([0x00]), ProtocolFamily.V5G)))

    def test_build_v5g_text_job_still_uses_source_image_mode_packet(self) -> None:
        data = self.builders._build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=True,
            speed=None,
            energy=15000,
            density=None,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.V5G,
            feed_padding=12,
            dev_dpi=203,
            post_print_feed_count=1,
            image_pipeline=self.v5g_dot,
        )

        self.assertIn(self.commands.make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G), data)
        self.assertNotIn(self.commands.make_packet(0xBE, bytes([0x01]), ProtocolFamily.V5G), data)

    def test_build_v5g_gray_job_uses_density_and_compressed_frame(self) -> None:
        raster_set = self._raster_set(
            self._bw_raster([1, 0, 1, 0, 1, 0, 1, 0] * 2),
            self.raster.RasterBuffer(
                pixels=[15, 14, 13, 12, 11, 10, 9, 8] * 2,
                width=8,
                pixel_format=self.raster.PixelFormat.GRAY4,
            ),
        )

        with patch(
            "timiniprint.protocol.families.v5g.compress_lzo1x_1",
            return_value=bytes.fromhex("AABB"),
        ):
            data = self.builders._build_job_from_raster_set(
                raster_set=raster_set,
                is_text=False,
                speed=None,
                energy=15000,
                density=200,
                blackening=4,
                lsb_first=True,
                protocol_family=ProtocolFamily.V5G,
                feed_padding=12,
                dev_dpi=203,
                post_print_feed_count=3,
                image_pipeline=self.v5g_gray,
            )

        density_packet = self.commands.make_packet(
            0xF2,
            encode_density_payload(200),
            ProtocolFamily.V5G,
        )
        self.assertIn(density_packet, data)
        self.assertLess(
            data.index(density_packet),
            data.index(self.commands.make_packet(0xA3, bytes([0x00]), ProtocolFamily.V5G)),
        )
        self.assertIn(
            self.commands.make_packet(0xCF, bytes.fromhex("08000200AABB"), ProtocolFamily.V5G),
            data,
        )
        self.assertEqual(
            data.count(self.commands.make_packet(0xBD, bytes([0x0A]), ProtocolFamily.V5G)),
            1,
        )
        self.assertEqual(
            data.count(self.commands.make_packet(0xBD, bytes([0x19]), ProtocolFamily.V5G)),
            1,
        )
        self.assertGreaterEqual(
            data.count(self.commands.make_packet(0xA1, bytes([0x30, 0x00]), ProtocolFamily.V5G)),
            3,
        )


if __name__ == "__main__":
    unittest.main()
