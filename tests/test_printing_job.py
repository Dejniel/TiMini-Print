from __future__ import annotations

import importlib
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.helpers import install_crc8_stub, reset_registry_cache
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition
from timiniprint.protocol.types import ImageEncoding, PaperMode
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet
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
        builder_mod = importlib.import_module("timiniprint.printing.builder")
        settings_mod = importlib.import_module("timiniprint.printing.settings")
        cls.job_mod = types.SimpleNamespace(
            PrintJobBuilder=builder_mod.PrintJobBuilder,
            PrintSettings=settings_mod.PrintSettings,
        )

    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()
        self.profile = self.catalog.require_profile("x6h")
        self.device = self.catalog.device_from_profile("x6h")

    def _family_device(self, family: ProtocolFamily) -> object:
        return replace(
            self.device,
            protocol_family=family,
            image_pipeline=get_protocol_definition(family).behavior.default_image_pipeline,
        )

    def test_static_helpers(self) -> None:
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(384), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(386), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._mm_to_px(0, 203), 0)
        self.assertGreater(self.job_mod.PrintJobBuilder._mm_to_px(5, 203), 0)

    def test_build_from_file_validation(self) -> None:
        builder = self.job_mod.PrintJobBuilder(self.device, page_loader=_FakeLoader([]))
        with self.assertRaises(ValueError):
            builder.build_from_file("bad.unsupported")
        with self.assertRaises(FileNotFoundError):
            builder.build_from_file("missing.txt")

    def test_build_from_file_uses_loader_renderer_and_build_job(self) -> None:
        img = Image.new("1", (8, 1), 1)
        pages = [Page(img, dither=False, is_text=False), Page(img, dither=True, is_text=True)]
        loader = _FakeLoader(pages)
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(paper_mode=PaperMode.TAG),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set), patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set", side_effect=[(b"A", ()), (b"B", ())]
            ) as build_job_mock:
                out = builder.build_from_file(str(path))
        self.assertEqual(out.payload, b"AB")
        self.assertEqual(out.payload_segments, (b"A", b"B"))
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["paper_mode"], PaperMode.TAG)
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["page_index"], 1)
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["page_count"], 2)
        self.assertEqual(build_job_mock.call_args_list[1].kwargs["page_index"], 2)
        self.assertEqual(build_job_mock.call_args_list[1].kwargs["page_count"], 2)

    def test_build_from_file_applies_debug_row_markers_before_protocol_build(self) -> None:
        img = Image.new("1", (16, 12), 1)
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(debug_row_markers_interval=10),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(
                    pixels=[0] * (16 * 12),
                    width=16,
                    pixel_format=PixelFormat.BW1,
                )
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.printing.builder.render_raster_set",
                return_value=raster_set,
            ), patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
        marked = build_job_mock.call_args.kwargs["raster_set"].require(PixelFormat.BW1)
        self.assertEqual(marked.pixels[16], 1)
        self.assertEqual(marked.pixels[31], 1)
        self.assertGreater(sum(marked.pixels[160:176]), 2)

    def test_build_from_file_can_rotate_pages_clockwise_for_any_file_type(self) -> None:
        img = Image.new("1", (8, 16), 1)
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(rotate_90_clockwise=True),
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
        rotated = render_mock.call_args.args[0]
        self.assertEqual(rotated.size, (16, 8))

    def test_mode_energy_speed_selection(self) -> None:
        settings = self.job_mod.PrintSettings(text_mode=None, lsb_first=None)
        builder = self.job_mod.PrintJobBuilder(self.device, settings=settings, page_loader=_FakeLoader([]))
        p_text = Page(Image.new("1", (8, 1), 1), dither=False, is_text=True)
        p_img = Page(Image.new("1", (8, 1), 1), dither=True, is_text=False)
        self.assertTrue(builder._select_text_mode(p_text))
        self.assertFalse(builder._select_text_mode(p_img))
        self.assertGreater(
            builder.device.profile.select_energy(is_text=True, blackening=builder.settings.blackening),
            0,
        )
        self.assertGreater(
            builder.device.profile.select_energy(is_text=False, blackening=builder.settings.blackening),
            0,
        )

    def test_v5g_runtime_controller_uses_runtime_defaults(self) -> None:
        resolved = self.catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(resolved)
        builder = self.job_mod.PrintJobBuilder(
            resolved,
            page_loader=_FakeLoader([Page(Image.new("1", (8, 1), 1), dither=True, is_text=False)]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch("timiniprint.printing.builder.render_raster_set", return_value=RasterSet(
                rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
            )), patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_model_mock:
                job = builder.build_from_file(str(path))

        controller = job.runtime_controller
        self.assertIsNotNone(controller)
        snapshot = controller.debug_snapshot()
        self.assertEqual(snapshot["helper_kind"], "mx10")
        self.assertEqual(snapshot["runtime_defaults_key"], "mx06")
        self.assertEqual(snapshot["runtime_defaults"]["key"], "mx06")
        self.assertTrue(snapshot["capabilities"]["d2_status"])
        self.assertFalse(snapshot["capabilities"]["didian_status"])
        self.assertEqual(snapshot["density_levels"]["image"]["middle"], 180)
        self.assertEqual(snapshot["density_levels"]["text"]["middle"], 130)
        self.assertEqual(build_model_mock.call_args.kwargs["density"], 180)
        self.assertIsNone(
            builder.device.profile.select_density(
                is_text=False,
                blackening=builder.settings.blackening,
            )
        )
        self.assertIsNone(
            builder.device.profile.select_density(
                is_text=True,
                blackening=builder.settings.blackening,
            )
        )

    def test_v5g_runtime_controller_can_use_xopoppy_runtime_defaults(self) -> None:
        resolved = self.catalog.detect_device("XOPOPPY-ABCD", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(resolved)
        builder = self.job_mod.PrintJobBuilder(resolved, page_loader=_FakeLoader([]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch("timiniprint.printing.builder.render_raster_set", return_value=RasterSet(
                rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
            )), patch("timiniprint.protocol.job._build_job_model_from_raster_set", return_value=(b"A", ())):
                job = builder.build_from_file(str(path))

        controller = job.runtime_controller
        self.assertIsNotNone(controller)
        snapshot = controller.debug_snapshot()
        self.assertEqual(snapshot["runtime_defaults"]["key"], "xopoppy")
        self.assertEqual(snapshot["density_levels"]["image"]["middle"], 80)
        self.assertEqual(snapshot["density_levels"]["text"]["middle"], 80)

    def test_build_from_file_uses_v5c_default_bw1_pipeline(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        builder = self.job_mod.PrintJobBuilder(
            self._family_device(ProtocolFamily.V5C),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
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
            self._family_device(ProtocolFamily.V5C),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
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
            self._family_device(ProtocolFamily.V5C),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
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
            self._family_device(ProtocolFamily.V5C),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY4,))
        self.assertFalse(render_mock.call_args.kwargs["gamma_handle"])

    def test_build_from_file_auto_selects_v5c_gray4_for_a5_override(self) -> None:
        img = Image.new("L", (8, 1))
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(image_encoding_override=ImageEncoding.V5C_A5)
        builder = self.job_mod.PrintJobBuilder(
            self._family_device(ProtocolFamily.V5C),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY4,))
        self.assertEqual(build_job_mock.call_args.kwargs["image_pipeline"].encoding, ImageEncoding.V5C_A5)
        self.assertEqual(build_job_mock.call_args.kwargs["image_pipeline"].default_format, PixelFormat.GRAY4)

    def test_build_from_file_can_override_image_encoding(self) -> None:
        img = Image.new("1", (8, 1), 1)
        loader = _FakeLoader([Page(img, dither=False, is_text=False)])
        settings = self.job_mod.PrintSettings(image_encoding_override=ImageEncoding.LEGACY_RLE)
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=settings,
            page_loader=loader,
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 8, width=8, pixel_format=PixelFormat.BW1)}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(b"x")
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set), patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
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
            self._family_device(ProtocolFamily.V5X),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
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
            self._family_device(ProtocolFamily.V5X),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
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
            self._family_device(ProtocolFamily.V5X),
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
            with patch("timiniprint.printing.builder.render_raster_set", return_value=raster_set) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                builder.build_from_file(str(path))

        self.assertEqual(render_mock.call_args.args[1], (PixelFormat.GRAY8,))
        self.assertTrue(render_mock.call_args.kwargs["gamma_handle"])
        self.assertEqual(render_mock.call_args.kwargs["gamma_value"], 1.1)


if __name__ == "__main__":
    unittest.main()
