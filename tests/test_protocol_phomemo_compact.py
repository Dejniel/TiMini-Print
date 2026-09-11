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
from timiniprint.raster import DitherMode, PixelFormat, RasterBuffer, RasterSet


_PROFILES = {
    "phomemo_m02": (384, 203, 4, 0),
    "phomemo_m02s": (576, 300, 4, 12),
    "phomemo_m02_pro": (576, 300, 4, 7),
    "phomemo_t02": (384, 203, 4, 0),
    "phomemo_m110": (384, 203, 0, 0),
    "phomemo_m220": (576, 203, 0, 0),
}
_SETUP = bytes.fromhex("1b40 1f110202 1f113764 1f110b 1f113500")
_FEED = bytes.fromhex("1b6402")


def _raster(width: int, height: int = 2) -> RasterSet:
    pixels = [0] * (width * height)
    pixels[0] = pixels[-1] = 1
    return RasterSet.from_single(RasterBuffer(pixels, width, PixelFormat.BW1))


def _expected_raster(width: int, height: int, first: int, last: int) -> bytes:
    row_bytes = (width + 7) // 8
    rows = bytearray(row_bytes * height)
    rows[first // 8] |= 1 << (7 - first % 8)
    rows[(height - 1) * row_bytes + last // 8] |= 1 << (7 - last % 8)
    return (
        bytes.fromhex("1d763000") + row_bytes.to_bytes(2, "little")
        + height.to_bytes(2, "little") + rows
    )


class PhomemoCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = PrinterCatalog.load()

    def test_model_geometry_and_exact_aliases(self) -> None:
        for key, (width, dpi, _left, _extra) in _PROFILES.items():
            with self.subTest(profile=key):
                device = self.catalog.device_from_profile(key)
                self.assertEqual(device.profile.dev_dpi, dpi)
                self.assertEqual(device.profile.default_paper_preset.render_width_px, width)
                self.assertTrue(device.profile.use_spp)
                self.assertEqual(device.profile.stream.chunk_size, 128)
                self.assertEqual(device.profile.stream.delay_ms, 20)
                self.assertEqual(device.profile.select_speed(is_text=False), None)
        for name, key in (("M02C", "phomemo_m02"), ("sandymaro", "phomemo_m02_pro")):
            with self.subTest(name=name):
                self.assertEqual(self.catalog.detect_device(name).profile_key, key)
                self.assertIsNone(self.catalog.detect_device(name + "-1234"))

    def test_wire_placement_for_full_narrow_and_unaligned_content(self) -> None:
        for key, (maximum, _dpi, left, extra) in _PROFILES.items():
            protocol = PrinterProtocol(self.catalog.device_from_profile(key))
            for width in (1, 7, 8, 17, maximum - 1, maximum):
                for mode in protocol.supported_paper_modes():
                    with self.subTest(profile=key, width=width, mode=mode):
                        wire_width = maximum + extra + left if extra and mode is PaperMode.TAG else width + left
                        expected = _expected_raster(wire_width, 2, left, left + width - 1)
                        setup = _SETUP.replace(bytes.fromhex("1f110b"), bytes.fromhex("1f1126")) if mode is PaperMode.BLACK_TAG else _SETUP
                        job = protocol.build_job(_raster(width), is_text=False, paper_mode=mode)
                        self.assertEqual(job.payload, setup + expected + _FEED * 2)
            with self.assertRaisesRegex(ValueError, "content width"):
                protocol.build_job(_raster(maximum + 1), is_text=False)
            with self.assertRaises(ValueError):
                protocol.build_job(_raster(8), is_text=False, paper_mode=PaperMode.DOCUMENT)

    def test_all_density_levels_and_coefficient(self) -> None:
        for key in _PROFILES:
            device = self.catalog.device_from_profile(key)
            for level, density, coefficient in ((1, 1, 100), (2, 2, 100), (3, 4, 100), (4, 4, 150)):
                levels = LevelProfile(level, level, level)
                profile = replace(device.profile, print_defaults=replace(
                    device.profile.print_defaults, density=ModeLevelProfile(levels, levels),
                ))
                protocol = PrinterProtocol(replace(device, profile=profile))
                for is_text in (False, True):
                    with self.subTest(profile=key, level=level, text=is_text):
                        job = protocol.build_job(_raster(8), is_text=is_text)
                        self.assertEqual(job.payload[:10], bytes.fromhex("1b40 1f1102") + bytes([density]) + bytes.fromhex("1f1137") + bytes([coefficient]))

    def test_setup_and_finish_follow_document_boundaries(self) -> None:
        for key in _PROFILES:
            protocol = PrinterProtocol(self.catalog.device_from_profile(key))
            for flow in PageFlow:
                for page in (1, 2, 3):
                    with self.subTest(profile=key, flow=flow, page=page):
                        payload = protocol.build_job(
                            _raster(8), is_text=False, page_index=page, page_count=3, page_flow=flow,
                        ).payload
                        self.assertEqual(payload.count(bytes.fromhex("1b40")), int(page == 1))
                        expected_feeds = 2 if page == 3 else int(flow is PageFlow.PAGED)
                        self.assertEqual(payload.count(_FEED), expected_feeds)

    def test_image_to_job_preserves_pixels_with_every_preset(self) -> None:
        for key, (width, _dpi, left, extra) in _PROFILES.items():
            device = self.catalog.device_from_profile(key)
            for preset in device.profile.paper_presets:
                with self.subTest(profile=key, preset=preset.key):
                    image = Image.new("RGB", (width, 2), "white")
                    image.putpixel((0, 0), (0, 0, 0))
                    image.putpixel((width - 1, 1), (0, 0, 0))
                    builder = PrintJobBuilder(device, settings=PrintSettings(
                        paper_preset_key=preset.key, dither_mode=DitherMode.NONE,
                        trim_side_margins=False, trim_top_bottom_margins=False, feed_padding=0,
                    ), document_renderer=DocumentRenderer(image_loader=lambda _path: image))
                    with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
                        job = builder.build_from_file("image.png")
                    wire_width = width + left + (extra if preset.paper_mode is PaperMode.TAG else 0)
                    expected = _expected_raster(wire_width, 2, left, left + width - 1)
                    setup = _SETUP.replace(bytes.fromhex("1f110b"), bytes.fromhex("1f1126")) if preset.paper_mode is PaperMode.BLACK_TAG else _SETUP
                    self.assertEqual(job.payload, setup + expected + _FEED * 2)

    def test_height_over_16_bits_splits_without_extra_feed_or_reset(self) -> None:
        for key, (_width, _dpi, left, _extra) in _PROFILES.items():
            with self.subTest(profile=key):
                job = PrinterProtocol(self.catalog.device_from_profile(key)).build_job(
                    _raster(1, 65536), is_text=False, paper_mode=PaperMode.PLAIN,
                )
                first = bytes.fromhex("1d7630000100ffff") + bytes([1 << (7 - left)]) + bytes(65534)
                last = bytes.fromhex("1d76300001000100") + bytes([1 << (7 - left)])
                self.assertEqual(job.payload, _SETUP + first + last + _FEED * 2)


if __name__ == "__main__":
    unittest.main()
