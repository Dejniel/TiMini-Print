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
from timiniprint.printing.document_renderer import DocumentPage, DocumentPlan, RenderDocument, RenderedPage
from timiniprint.protocol import ImagePipelineConfig
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition
from timiniprint.protocol.job import PrinterProtocol
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.protocol.types import PaperMode
from timiniprint.raster import DitherMode, PixelFormat, RasterBuffer, RasterSet
from timiniprint.rendering.converters.base import Page
from timiniprint.rendering.formats import mm_to_px, normalized_width


class _FakeDocumentRenderer:
    def __init__(self, pages: list[RenderedPage]) -> None:
        self.pages = pages
        self.planned_documents: list[RenderDocument] = []
        self.printed_pages: list[int] = []
        self.runtime_capabilities: list[RuntimePrintCapabilities | None] = []

    def plan_document(self, document, device, settings):
        _ = device, settings
        self.planned_documents.append(document)
        return DocumentPlan(
            document=document,
            kind="text",
            pages=tuple(DocumentPage(index) for index in range(len(self.pages))),
            source_page_count=len(self.pages),
        )

    def print_page(self, plan, page, device, settings, runtime_capabilities=None):
        _ = plan, device, settings
        self.printed_pages.append(page.number)
        self.runtime_capabilities.append(runtime_capabilities)
        return self.pages[page.index]


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

    def test_rendering_format_helpers(self) -> None:
        self.assertEqual(normalized_width(384), 384)
        self.assertEqual(normalized_width(386), 384)
        self.assertEqual(mm_to_px(0, 203), 0)
        self.assertGreater(mm_to_px(5, 203), 0)

    def test_build_from_file_validation(self) -> None:
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            document_renderer=_FakeDocumentRenderer([]),
        )
        with self.assertRaises(ValueError):
            builder.build_from_file("bad.unsupported")
        with self.assertRaises(FileNotFoundError):
            builder.build_from_file("missing.txt")

    def test_build_from_file_uses_document_renderer_and_build_job(self) -> None:
        pipeline = PrinterProtocol(self.device).resolve_image_pipeline()
        renderer = _FakeDocumentRenderer(
            [
                _rendered_page(pipeline=pipeline, is_text=False),
                _rendered_page(pipeline=pipeline, is_text=True),
            ]
        )
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(paper_mode=PaperMode.TAG),
            document_renderer=renderer,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                side_effect=[(b"A", ()), (b"B", ())],
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"AB")
        self.assertEqual(out.payload_segments, (b"A", b"B"))
        self.assertEqual(renderer.planned_documents[0].source, str(path))
        self.assertEqual(renderer.printed_pages, [1, 2])
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["paper_mode"], PaperMode.TAG)
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["page_index"], 1)
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["page_count"], 2)
        self.assertEqual(build_job_mock.call_args_list[0].kwargs["image_pipeline"], pipeline)
        self.assertFalse(build_job_mock.call_args_list[0].kwargs["is_text"])
        self.assertTrue(build_job_mock.call_args_list[1].kwargs["is_text"])

    def test_iter_page_jobs_yields_page_jobs_without_combining(self) -> None:
        pipeline = PrinterProtocol(self.device).resolve_image_pipeline()
        renderer = _FakeDocumentRenderer(
            [
                _rendered_page(pipeline=pipeline, is_text=False),
                _rendered_page(pipeline=pipeline, is_text=True),
            ]
        )
        builder = self.job_mod.PrintJobBuilder(self.device, document_renderer=renderer)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                side_effect=[(b"A", ()), (b"B", ())],
            ), patch("timiniprint.printing.builder.combine_raster_page_jobs") as combine_mock:
                pages = list(builder.iter_page_jobs(str(path)))

        self.assertEqual([page.job.payload for page in pages], [b"A", b"B"])
        self.assertEqual([page.page_index for page in pages], [1, 2])
        self.assertEqual([page.page_count for page in pages], [2, 2])
        self.assertEqual([page.image_pipeline for page in pages], [pipeline, pipeline])
        combine_mock.assert_not_called()

    def test_build_from_file_applies_debug_row_markers_before_protocol_build(self) -> None:
        pipeline = PrinterProtocol(self.device).resolve_image_pipeline()
        raster_set = RasterSet(
            rasters={
                PixelFormat.BW1: RasterBuffer(
                    pixels=[0] * (16 * 12),
                    width=16,
                    pixel_format=PixelFormat.BW1,
                )
            }
        )
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(debug_row_markers_interval=10),
            document_renderer=_FakeDocumentRenderer(
                [_rendered_page(pipeline=pipeline, raster_set=raster_set)]
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_job_mock:
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
        marked = build_job_mock.call_args.kwargs["raster_set"].require(PixelFormat.BW1)
        self.assertEqual(marked.pixels[16], 1)
        self.assertEqual(marked.pixels[31], 1)
        self.assertGreater(sum(marked.pixels[160:176]), 2)

    def test_builder_passes_runtime_capabilities_to_renderer(self) -> None:
        device = self._family_device(ProtocolFamily.V5C)
        pipeline = PrinterProtocol(device).resolve_image_pipeline()
        capabilities = RuntimePrintCapabilities(supports_gray=False)
        renderer = _FakeDocumentRenderer([_rendered_page(pipeline=pipeline)])
        builder = self.job_mod.PrintJobBuilder(
            device,
            document_renderer=renderer,
            runtime_context=types.SimpleNamespace(
                capabilities=capabilities,
                runtime_controller=None,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                builder.build_from_file(str(path))

        self.assertEqual(renderer.runtime_capabilities, [capabilities])

    def test_build_from_file_uses_real_renderer_when_needed(self) -> None:
        builder = self.job_mod.PrintJobBuilder(
            self.device,
            settings=self.job_mod.PrintSettings(
                rotate_90_clockwise=True,
                trim_side_margins=False,
                trim_top_bottom_margins=False,
            ),
        )
        raster_set = RasterSet(
            rasters={PixelFormat.BW1: RasterBuffer(pixels=[1] * 384, width=384, pixel_format=PixelFormat.BW1)}
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            Image.new("RGB", (800, 200), "black").save(path)
            with patch(
                "timiniprint.printing.document_renderer.PrintImageRenderer.raster_set",
                return_value=raster_set,
            ) as render_mock, patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                out = builder.build_from_file(str(path))

        self.assertEqual(out.payload, b"A")
        rotated = render_mock.call_args.args[0]
        self.assertEqual(rotated.size, (384, 1536))

    def test_v5g_runtime_controller_uses_runtime_preset(self) -> None:
        resolved = self.catalog.detect_device("MX10", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(resolved)
        pipeline = PrinterProtocol(resolved).resolve_image_pipeline()
        builder = self.job_mod.PrintJobBuilder(
            resolved,
            document_renderer=_FakeDocumentRenderer(
                [_rendered_page(pipeline=pipeline, is_text=False)]
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ) as build_model_mock:
                job = builder.build_from_file(str(path))

        controller = job.runtime_controller
        self.assertIsNotNone(controller)
        snapshot = controller.debug_snapshot()
        self.assertEqual(snapshot["helper_kind"], "mx10")
        self.assertEqual(snapshot["profile_runtime_preset_key"], "mx10_mx06")
        self.assertEqual(snapshot["runtime_preset"]["key"], "mx10_mx06")
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

    def test_v5g_runtime_controller_can_use_xopoppy_runtime_preset(self) -> None:
        resolved = self.catalog.detect_device("XOPOPPY", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(resolved)
        pipeline = PrinterProtocol(resolved).resolve_image_pipeline()
        builder = self.job_mod.PrintJobBuilder(
            resolved,
            document_renderer=_FakeDocumentRenderer([_rendered_page(pipeline=pipeline)]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch(
                "timiniprint.protocol.job._build_job_model_from_raster_set",
                return_value=(b"A", ()),
            ):
                job = builder.build_from_file(str(path))

        controller = job.runtime_controller
        self.assertIsNotNone(controller)
        snapshot = controller.debug_snapshot()
        self.assertEqual(snapshot["runtime_preset"]["key"], "xopoppy")
        self.assertEqual(snapshot["density_levels"]["image"]["middle"], 80)
        self.assertEqual(snapshot["density_levels"]["text"]["middle"], 80)


def _rendered_page(
    *,
    pipeline: ImagePipelineConfig,
    raster_set: RasterSet | None = None,
    is_text: bool = False,
    dither_mode: DitherMode = DitherMode.NONE,
) -> RenderedPage:
    image = Image.new("1", (8, 1), 1)
    return RenderedPage(
        source_page=Page(image, dither=dither_mode != DitherMode.NONE, is_text=is_text),
        raster_set=raster_set or _raster_set(pipeline.default_format),
        image_pipeline=pipeline,
        is_text=is_text,
        dither_mode=dither_mode,
    )


def _raster_set(pixel_format: PixelFormat) -> RasterSet:
    return RasterSet(
        rasters={
            pixel_format: RasterBuffer(
                pixels=[1] * 8,
                width=8,
                pixel_format=pixel_format,
            )
        }
    )


if __name__ == "__main__":
    unittest.main()
