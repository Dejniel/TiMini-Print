from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import LevelProfile, ModeLevelProfile
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentRenderer
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PageFlow, PaperMode, PrinterProtocol
from timiniprint.protocol.families.phomemo_esc.flow import PhomemoPageStep
from timiniprint.raster import DitherMode, PixelFormat, RasterBuffer, RasterSet

_PROFILES = {
    "phomemo_m02": (384, 203, 0),
    "phomemo_m02s": (576, 300, 12),
    "phomemo_m02_pro": (576, 300, 7),
    "phomemo_t02": (384, 203, 0),
    "phomemo_m02x": (384, 203, 0),
}
_SETUP = bytes.fromhex("1b40 1f110202 1f113764 1f110b 1f113500")
_FEED = bytes.fromhex("1b6402")


def _raster(width, height=2):
    pixels = [0] * (width * height)
    pixels[0] = pixels[-1] = 1
    return RasterSet.from_single(RasterBuffer(pixels, width, PixelFormat.BW1))


def _expected(width, height, left, content_width):
    row_bytes = (width + 7) // 8
    rows = bytearray(row_bytes * (height + 4))
    rows[4 * row_bytes + left // 8] |= 1 << (7 - left % 8)
    last = left + content_width - 1
    rows[(height + 3) * row_bytes + last // 8] |= 1 << (7 - last % 8)
    return bytes.fromhex("1d763000") + row_bytes.to_bytes(2, "little") + (height + 4).to_bytes(2, "little") + rows


class PhomemoCompactTests(unittest.TestCase):
    def test_paginated_recipe_hook_preserves_setup_and_waits_without_generic_feed(self):
        from timiniprint.protocol.families.phomemo_esc.compact import PhomemoCompactRecipe
        from timiniprint.protocol.families.phomemo_esc.core import RECIPES

        class SheetRecipe(PhomemoCompactRecipe):
            def _media_command(self, paper_mode, request):
                return bytes.fromhex("1f1138")

        recipe = SheetRecipe(384, paper_modes=(PaperMode.TATTOO,), paginated_modes=(PaperMode.TATTOO,))
        device = PrinterCatalog.load().device_from_model("phomemo_m02")
        paper = replace(device.profile.default_paper_preset, paper_mode=PaperMode.TATTOO)
        device = device.with_print_profile(replace(device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key))
        with patch.dict(RECIPES, {"m02": recipe}):
            for page in (1, 2):
                job = PrinterProtocol(device).build_job(_raster(8), is_text=False, paper_mode=PaperMode.TATTOO,
                                                        page_index=page, page_count=2)
                self.assertEqual(job.payload.count(bytes.fromhex("1f1138")), int(page == 1))
                self.assertNotIn(_FEED, job.payload)
                self.assertTrue(job.steps[0].paginated)
                self.assertTrue(job.steps[0].wait_for_result)
                self.assertIsNone(job.steps[0].completion_delay_sec)

    def setUp(self):
        self.catalog = PrinterCatalog.load()

    def test_geometry_and_exact_aliases(self):
        for key, (width, dpi, _) in _PROFILES.items():
            device = self.catalog.device_from_profile(key)
            self.assertEqual(device.profile.dev_dpi, dpi)
            self.assertEqual(device.profile.default_paper_preset.render_width_px, width)
            self.assertTrue(device.profile.use_spp)
        for name, key in (("M02C", "phomemo_m02"), ("sandymaro", "phomemo_m02_pro")):
            self.assertEqual(self.catalog.detect_device(name).profile_key, key)
            self.assertIsNone(self.catalog.detect_device(name + "-1234"))

    def test_top_border_and_label_left_alignment_for_every_width(self):
        for key, (maximum, _, extra) in _PROFILES.items():
            protocol = PrinterProtocol(self.catalog.device_from_profile(key))
            for width in (1, 7, 8, 17, maximum - 1, maximum):
                for mode in protocol.supported_paper_modes():
                    with self.subTest(profile=key, width=width, mode=mode):
                        left = maximum + extra - width if extra and mode is PaperMode.TAG else 0
                        expected = _expected(width + left, 2, left, width)
                        job = protocol.build_job(_raster(width), is_text=False, paper_mode=mode)
                        self.assertEqual(job.payload, _SETUP + expected + _FEED * 2)
                        self.assertIsInstance(job.steps[0], PhomemoPageStep)
            with self.assertRaisesRegex(ValueError, "content width"):
                protocol.build_job(_raster(maximum + 1), is_text=False)

    def test_asymmetric_wire_vectors(self):
        image = RasterSet.from_single(RasterBuffer([1] + [0] * 127, 16, PixelFormat.BW1))
        for key, mode, header, offset, value in (
            ("phomemo_m02", PaperMode.PLAIN, "1d76300002000c00", 8, 0x80),
            ("phomemo_m02s", PaperMode.TAG, "1d7630004a000c00", 367, 0x08),
            ("phomemo_m02_pro", PaperMode.TAG, "1d76300049000c00", 362, 0x01),
        ):
            payload = PrinterProtocol(self.catalog.device_from_profile(key)).build_job(
                image, is_text=False, paper_mode=mode,
            ).payload
            start = len(_SETUP)
            self.assertEqual(payload[start:start + 8], bytes.fromhex(header))
            body = payload[start + 8:-6]
            self.assertEqual([(i, b) for i, b in enumerate(body) if b], [(offset, value)])

    def test_all_density_levels(self):
        for key in _PROFILES:
            device = self.catalog.device_from_profile(key)
            for level, density, coefficient in ((1, 1, 100), (2, 2, 100), (3, 4, 100), (4, 4, 150)):
                levels = LevelProfile(level, level, level)
                profile = replace(device.profile, print_defaults=replace(
                    device.profile.print_defaults, density=ModeLevelProfile(levels, levels),
                ))
                for text in (False, True):
                    job = PrinterProtocol(replace(device, profile=profile)).build_job(_raster(8), is_text=text)
                    self.assertEqual(job.payload[:10], bytes.fromhex("1b40 1f1102") + bytes([density])
                                     + bytes.fromhex("1f1137") + bytes([coefficient]))

    def test_page_boundaries_keep_one_setup_and_correct_feeds(self):
        for key in _PROFILES:
            protocol = PrinterProtocol(self.catalog.device_from_profile(key))
            for flow in PageFlow:
                for page in (1, 2, 3):
                    job = protocol.build_job(_raster(8), is_text=False, page_index=page, page_count=3, page_flow=flow)
                    self.assertEqual(job.payload.count(bytes.fromhex("1b40")), int(page == 1))
                    self.assertEqual(job.payload.count(_FEED), 2 if page == 3 else int(flow is PageFlow.PAGED))
                    self.assertIsNotNone(job.steps[0].completion_delay_sec)

    def test_render_to_wire_does_not_double_apply_alignment(self):
        for key, (width, _, extra) in _PROFILES.items():
            device = self.catalog.device_from_profile(key)
            for preset in device.profile.paper_presets:
                image = Image.new("RGB", (width, 2), "white")
                image.putpixel((0, 0), (0, 0, 0))
                image.putpixel((width - 1, 1), (0, 0, 0))
                builder = PrintJobBuilder(device, settings=PrintSettings(
                    paper_preset_key=preset.key, dither_mode=DitherMode.NONE,
                    trim_side_margins=False, trim_top_bottom_margins=False, feed_padding=0,
                ), document_renderer=DocumentRenderer(image_loader=lambda _: image))
                with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
                    job = builder.build_from_file("image.png")
                left = extra if preset.paper_mode is PaperMode.TAG else 0
                self.assertEqual(job.payload, _SETUP + _expected(width + left, 2, left, width) + _FEED * 2)

    def test_large_height_splits_without_repeating_setup_or_top_border(self):
        protocol = PrinterProtocol(self.catalog.device_from_profile("phomemo_m02"))
        job = protocol.build_job(_raster(8, 65535), is_text=False)
        first = len(_SETUP)
        self.assertEqual(job.payload[first:first + 8], bytes.fromhex("1d7630000100ffff"))
        second = first + 8 + 65535
        self.assertEqual(job.payload[second:second + 8], bytes.fromhex("1d76300001000400"))
        self.assertEqual(job.payload.count(_FEED), 2)
