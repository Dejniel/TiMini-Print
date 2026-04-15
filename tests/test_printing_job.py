from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.helpers import install_crc8_stub, reset_registry_cache
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding, PixelFormat, RasterBuffer, RasterSet
from timiniprint.rendering.converters.base import Page


class _FakeLoader:
    supported_extensions = {".txt", ".png"}

    def __init__(self, pages):
        self._pages = pages

    def load(self, path: str, width: int):
        _ = path, width
        return list(self._pages)


class PrintingJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.job_mod = importlib.import_module("timiniprint.printing.job")

    def setUp(self) -> None:
        reset_registry_cache()
        self.profile = PrinterCatalog.load().require_profile("x6h")

    def test_static_helpers(self) -> None:
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(384), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(386), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._mm_to_px(0, 203), 0)
        self.assertGreater(self.job_mod.PrintJobBuilder._mm_to_px(5, 203), 0)

    def test_build_from_file_validation(self) -> None:
        builder = self.job_mod.PrintJobBuilder(self.profile, page_loader=_FakeLoader([]))
        with self.assertRaises(ValueError):
            builder.build_from_file("bad.unsupported")
        with self.assertRaises(FileNotFoundError):
            builder.build_from_file("missing.txt")

    def test_build_from_file_uses_loader_renderer_and_build_job(self) -> None:
        img = Image.new("1", (8, 1), 1)
        pages = [Page(img, dither=False, is_text=False), Page(img, dither=True, is_text=True)]
        loader = _FakeLoader(pages)
        builder = self.job_mod.PrintJobBuilder(self.profile, page_loader=loader)
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set), patch(
                "timiniprint.printing.job.build_job_from_raster_set", side_effect=[b"A", b"B"]
            ):
                out = builder.build_from_file(str(path))
        self.assertEqual(out, b"AB")

    def test_build_from_file_can_rotate_pages_clockwise_for_any_file_type(self) -> None:
        img = Image.new("1", (8, 16), 1)
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            settings=self.job_mod.PrintSettings(rotate_90_clockwise=True),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ):
                out = builder.build_from_file(str(path))

        self.assertEqual(out, b"A")
        rotated = render_mock.call_args.args[0]
        self.assertEqual(rotated.size, (16, 8))

    def test_mode_energy_speed_selection(self) -> None:
        settings = self.job_mod.PrintSettings(text_mode=None, lsb_first=None)
        builder = self.job_mod.PrintJobBuilder(self.profile, settings=settings, page_loader=_FakeLoader([]))
        p_text = Page(Image.new("1", (8, 1), 1), dither=False, is_text=True)
        p_img = Page(Image.new("1", (8, 1), 1), dither=True, is_text=False)
        self.assertTrue(builder._select_text_mode(p_text))
        self.assertFalse(builder._select_text_mode(p_img))
        self.assertGreater(builder._select_energy(True), 0)
        self.assertGreater(builder._select_energy(False), 0)

    def test_v5g_dynamic_helper_uses_donor_density_profile(self) -> None:
        catalog = PrinterCatalog.load()
        profile = catalog.require_profile("v5g_small_203")
        donor = catalog.require_profile("mx06")
        builder = self.job_mod.PrintJobBuilder(
            profile,
            protocol_family=ProtocolFamily.V5G,
            v5g_dynamic_helper=self.job_mod.V5GDynamicHelper(
                helper_kind="mx10",
                density_profile_key="mx06",
            ),
            v5g_density_profile=donor,
            page_loader=_FakeLoader([]),
        )

        self.assertIsNotNone(builder.runtime_context)
        self.assertEqual(builder.runtime_context.helper_kind, "mx10")
        self.assertEqual(builder.runtime_context.density_profile_key, "mx06")
        self.assertEqual(builder.runtime_context.image_levels.middle, 180)
        self.assertEqual(builder.runtime_context.text_levels.middle, 130)
        self.assertEqual(builder._select_density(False), 180)
        self.assertEqual(builder._select_density(True), 130)

    def test_v5g_dynamic_helper_can_use_xopoppy_density_profile(self) -> None:
        catalog = PrinterCatalog.load()
        profile = catalog.require_profile("v5g_small_203")
        donor = catalog.require_profile("xopoppy")
        builder = self.job_mod.PrintJobBuilder(
            profile,
            protocol_family=ProtocolFamily.V5G,
            v5g_dynamic_helper=self.job_mod.V5GDynamicHelper(
                helper_kind="mx10",
                density_profile_key="xopoppy",
            ),
            v5g_density_profile=donor,
            page_loader=_FakeLoader([]),
        )

        self.assertIsNotNone(builder.runtime_context)
        self.assertEqual(builder.runtime_context.image_levels.middle, 80)
        self.assertEqual(builder.runtime_context.text_levels.middle, 80)

    def test_build_from_file_uses_v5c_default_bw1_pipeline(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5C,
            settings=self.job_mod.PrintSettings(),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
                PixelFormat.GRAY8: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY8),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out, b"A")
        self.assertEqual(
            render_mock.call_args.args[1],
            (PixelFormat.BW1,),
        )
        self.assertFalse(render_mock.call_args.kwargs["gamma_handle"])
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].encoding,
            ImageEncoding.V5C_A4,
        )
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].default_format,
            PixelFormat.BW1,
        )

    def test_build_from_file_can_override_v5c_compressed_pixel_format_to_gray8(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(
            image_encoding_override=ImageEncoding.V5C_A5,
            pixel_format_override=PixelFormat.GRAY8,
        )
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5C,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
                PixelFormat.GRAY8: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY8),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out, b"A")
        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY8,))
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].encoding,
            ImageEncoding.V5C_A5,
        )
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].default_format,
            PixelFormat.GRAY8,
        )

    def test_build_from_file_passes_explicit_v5c_gamma_value_for_a5(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(
            image_encoding_override=ImageEncoding.V5C_A5,
            pixel_format_override=PixelFormat.GRAY8,
            v5c_gamma_value=1.2,
        )
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5C,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
                PixelFormat.GRAY8: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY8),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY8,))
        self.assertEqual(render_mock.call_args.kwargs["gamma_value"], 1.2)

    def test_build_from_file_can_disable_v5c_gamma_processing_for_a5(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(
            image_encoding_override=ImageEncoding.V5C_A5,
            v5c_gamma_handle=False,
        )
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5C,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
                PixelFormat.GRAY8: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY8),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY4,))
        self.assertFalse(render_mock.call_args.kwargs["gamma_handle"])

    def test_build_from_file_auto_selects_v5c_gray4_for_a5_override(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(image_encoding_override=ImageEncoding.V5C_A5)
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5C,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out, b"A")
        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY4,))
        self.assertEqual(build_job_mock.call_args.kwargs["image_pipeline"].encoding, ImageEncoding.V5C_A5)
        self.assertEqual(build_job_mock.call_args.kwargs["image_pipeline"].default_format, PixelFormat.GRAY4)

    def test_build_from_file_can_override_image_encoding(self) -> None:
        img = Image.new("1", (8, 1), 1)
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(image_encoding_override=ImageEncoding.LEGACY_RLE)
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.LEGACY,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set), patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ) as build_job_mock:
                builder.build_from_file(str(path))

        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].encoding,
            ImageEncoding.LEGACY_RLE,
        )

    def test_build_from_file_uses_v5x_default_bw1_pipeline(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5X,
            settings=self.job_mod.PrintSettings(),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out, b"A")
        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.BW1,))
        self.assertFalse(render_mock.call_args.kwargs["gamma_handle"])
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].encoding,
            ImageEncoding.V5X_DOT,
        )
        self.assertEqual(
            build_job_mock.call_args.kwargs["image_pipeline"].default_format,
            PixelFormat.BW1,
        )

    def test_build_from_file_uses_v5x_gray_without_gamma_by_default(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(image_encoding_override=ImageEncoding.V5X_GRAY)
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5X,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY4,))
        self.assertFalse(render_mock.call_args.kwargs["gamma_handle"])
        self.assertIsNone(render_mock.call_args.kwargs["gamma_value"])

    def test_build_from_file_passes_explicit_v5x_gray_gamma_value(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(
            image_encoding_override=ImageEncoding.V5X_GRAY,
            pixel_format_override=PixelFormat.GRAY8,
            v5x_gamma_handle=True,
            v5x_gamma_value=1.1,
        )
        builder = self.job_mod.PrintJobBuilder(
            self.profile,
            protocol_family=ProtocolFamily.V5X,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1),
                PixelFormat.GRAY4: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY4),
                PixelFormat.GRAY8: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.GRAY8),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.job.image_to_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.printing.job.build_job_from_raster_set",
                return_value=b"A",
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY8,))
        self.assertTrue(render_mock.call_args.kwargs["gamma_handle"])
        self.assertEqual(render_mock.call_args.kwargs["gamma_value"], 1.1)


if __name__ == "__main__":
    unittest.main()
