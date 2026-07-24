from __future__ import annotations

import io
import unittest
from dataclasses import replace

from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import ImageEncoding, PageFlow, RuntimePrintCapabilities
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition
from timiniprint.raster import DitherMode, PixelFormat, RasterBuffer, RasterSet
from timiniprint.rendering.formats import document_kind


class RenderingDocumentRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = PrinterCatalog.load().detect_device("EMX-040256-ABCD")
        self.assertIsNotNone(self.device)

    def test_document_kind_uses_mime_then_display_name(self) -> None:
        self.assertEqual(document_kind("content://1", "image/png", "file.bin"), "image")
        self.assertEqual(document_kind("content://1", None, "label.webp"), "image")
        self.assertEqual(document_kind("content://1", None, "manual.pdf"), "pdf")
        self.assertEqual(document_kind("content://1", None, "notes.txt"), "text")

    def test_image_document_uses_injected_loader_only_when_rendering(self) -> None:
        calls = []

        def load_image(path: str) -> Image.Image:
            calls.append(path)
            return _test_image()

        renderer = DocumentRenderer(image_loader=load_image)
        plan = renderer.plan_document(
            RenderDocument("content://android/document/1", "application/octet-stream", "label.png"),
            self.device,
            PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False),
        )

        self.assertEqual(plan.kind, "image")
        self.assertEqual(plan.page_count, 1)
        self.assertEqual(calls, [])

        renderer.preview_page(plan, plan.pages[0], self.device, PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False))

        self.assertEqual(calls, ["content://android/document/1"])

    def test_preview_and_print_are_separate_outputs(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
            image_renderer=image_renderer,
        )
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), self.device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], self.device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], self.device, settings)

        self.assertEqual(preview.width, 384)
        self.assertEqual(rendered.raster_set.width, 384)
        self.assertEqual(image_renderer.preview_calls, 1)
        self.assertEqual(image_renderer.raster_calls, 1)

    def test_paper_preset_render_width_is_used_for_preview_and_print(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
            image_renderer=image_renderer,
        )
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    key="narrow",
                    label="Narrow roll",
                    paper_width_px=128,
                    render_width_px=128,
                ),
            ),
            default_paper_preset_key="narrow",
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(preview.width, 128)
        self.assertEqual(rendered.raster_set.width, 128)

    def test_paper_preset_centers_render_width_on_full_paper_width(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
        )
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    key="narrow",
                    label="Narrow on full roll",
                    paper_width_px=384,
                    render_width_px=128,
                ),
            ),
            default_paper_preset_key="narrow",
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)
        preview_image = Image.open(io.BytesIO(preview.png))

        self.assertEqual(preview.width, 384)
        self.assertEqual(rendered.raster_set.width, 384)
        self.assertEqual(preview_image.getpixel((0, 0)), 255)
        self.assertEqual(preview_image.getpixel((127, 0)), 255)
        self.assertEqual(preview_image.getpixel((128, 0)), 0)
        self.assertEqual(preview_image.getpixel((255, 0)), 0)
        self.assertEqual(preview_image.getpixel((256, 0)), 255)

    def test_paper_preset_left_padding_stays_protocol_side(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
        )
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    key="narrow",
                    label="Narrow with protocol padding",
                    paper_width_px=192,
                    render_width_px=128,
                    left_padding_px=64,
                ),
            ),
            default_paper_preset_key="narrow",
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(preview.width, 128)
        self.assertEqual(rendered.raster_set.width, 128)

    def test_paper_preset_exact_raster_height_is_used_for_preview_and_print(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
        )
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    raster_height_px=192,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)
        preview_image = Image.open(io.BytesIO(preview.png))

        self.assertEqual(preview.height, 192)
        self.assertEqual(rendered.raster_set.height, 192)
        self.assertEqual(preview_image.getpixel((0, 191)), 255)

    def test_paper_preset_fits_tall_page_to_fixed_render_area_before_padding(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: Image.new("RGB", (16, 16), "black"),
        )
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=128,
                    render_width_px=128,
                    render_height_px=64,
                    raster_height_px=96,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)
        preview_image = Image.open(io.BytesIO(preview.png))

        self.assertEqual((preview.width, preview.height), (128, 96))
        self.assertEqual((rendered.raster_set.width, rendered.raster_set.height), (128, 96))
        self.assertEqual(preview_image.getpixel((31, 0)), 255)
        self.assertEqual(preview_image.getpixel((32, 0)), 0)
        self.assertEqual(preview_image.getpixel((95, 63)), 0)
        self.assertEqual(preview_image.getpixel((96, 63)), 255)
        self.assertEqual(preview_image.getpixel((64, 64)), 255)

    def test_paper_preset_applies_top_padding_and_horizontal_mirror(self) -> None:
        source = Image.new("RGB", (8, 1), "white")
        source.putpixel((0, 0), (0, 0, 0))
        renderer = DocumentRenderer(image_loader=lambda _path: source)
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=8,
                    render_width_px=8,
                    top_padding_px=1,
                    raster_height_px=3,
                    mirror_horizontal=True,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], device, settings)
        preview_image = Image.open(io.BytesIO(preview.png))
        bw = rendered.raster_set.require(PixelFormat.BW1)

        self.assertEqual((preview.width, preview.height), (8, 3))
        self.assertEqual(preview_image.getpixel((0, 0)), 255)
        self.assertEqual(preview_image.getpixel((7, 1)), 0)
        self.assertEqual(
            list(bw.pixels),
            [0] * 8 + [0, 0, 0, 0, 0, 0, 0, 1] + [0] * 8,
        )

    def test_print_render_uses_runtime_capabilities(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
            image_renderer=image_renderer,
        )
        device = PrinterCatalog.load().detect_device("PPA2L_TEST")
        self.assertIsNotNone(device)
        settings = PrintSettings(
            image_encoding_override=ImageEncoding.LUCK_NORMAL_GRAY,
            pixel_format_override=PixelFormat.GRAY4,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        renderer.preview_page(plan, plan.pages[0], device, settings)
        renderer.print_page(
            plan,
            plan.pages[0],
            device,
            settings,
            runtime_capabilities=RuntimePrintCapabilities(supports_gray=False),
        )

        self.assertEqual(image_renderer.preview_formats, [PixelFormat.GRAY4])
        self.assertEqual(image_renderer.raster_formats, [(PixelFormat.BW1,)])

    def test_print_render_respects_text_mode_override(self) -> None:
        renderer = DocumentRenderer(image_loader=lambda _path: _test_image())
        settings = PrintSettings(
            text_mode=True,
            dither_mode=DitherMode.NONE,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), self.device, settings)

        rendered = renderer.print_page(plan, plan.pages[0], self.device, settings)

        self.assertTrue(rendered.is_text)

    def test_print_render_uses_v5c_default_bw1_pipeline(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image("L"),
            image_renderer=image_renderer,
        )
        device = self._family_device(ProtocolFamily.V5C)
        settings = PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False)
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(image_renderer.raster_formats, [(PixelFormat.BW1,)])
        self.assertEqual(rendered.image_pipeline.encoding, ImageEncoding.V5C_A4)
        self.assertEqual(rendered.image_pipeline.default_format, PixelFormat.BW1)
        self.assertFalse(rendered.gamma_handle)
        self.assertIsNone(rendered.gamma_value)

    def test_print_render_can_override_v5c_a5_to_gray8_with_gamma(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image("L"),
            image_renderer=image_renderer,
        )
        device = self._family_device(ProtocolFamily.V5C)
        settings = PrintSettings(
            image_encoding_override=ImageEncoding.V5C_A5,
            pixel_format_override=PixelFormat.GRAY8,
            v5c_gamma_value=1.2,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(image_renderer.raster_formats, [(PixelFormat.GRAY8,)])
        self.assertEqual(rendered.image_pipeline.encoding, ImageEncoding.V5C_A5)
        self.assertEqual(rendered.image_pipeline.default_format, PixelFormat.GRAY8)
        self.assertTrue(rendered.gamma_handle)
        self.assertEqual(rendered.gamma_value, 1.2)

    def test_print_render_can_disable_v5c_a5_gamma(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image("L"),
            image_renderer=image_renderer,
        )
        device = self._family_device(ProtocolFamily.V5C)
        settings = PrintSettings(
            image_encoding_override=ImageEncoding.V5C_A5,
            v5c_gamma_handle=False,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(image_renderer.raster_formats, [(PixelFormat.GRAY4,)])
        self.assertEqual(rendered.image_pipeline.default_format, PixelFormat.GRAY4)
        self.assertFalse(rendered.gamma_handle)
        self.assertIsNone(rendered.gamma_value)

    def test_print_render_uses_v5x_gray_gamma_override(self) -> None:
        image_renderer = _RecordingImageRenderer()
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image("L"),
            image_renderer=image_renderer,
        )
        device = self._family_device(ProtocolFamily.V5X)
        settings = PrintSettings(
            image_encoding_override=ImageEncoding.V5X_GRAY,
            pixel_format_override=PixelFormat.GRAY8,
            v5x_gamma_handle=True,
            v5x_gamma_value=1.1,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), device, settings)

        rendered = renderer.print_page(plan, plan.pages[0], device, settings)

        self.assertEqual(image_renderer.raster_formats, [(PixelFormat.GRAY8,)])
        self.assertEqual(rendered.image_pipeline.encoding, ImageEncoding.V5X_GRAY)
        self.assertEqual(rendered.image_pipeline.default_format, PixelFormat.GRAY8)
        self.assertTrue(rendered.gamma_handle)
        self.assertEqual(rendered.gamma_value, 1.1)

    def test_rotated_image_uses_full_print_width(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: Image.new("RGB", (800, 200), "black"),
        )
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            rotate_90_clockwise=True,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        plan = renderer.plan_document(RenderDocument("label.png"), self.device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], self.device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], self.device, settings)

        self.assertEqual((preview.width, preview.height), (384, 1536))
        self.assertEqual((rendered.raster_set.width, rendered.raster_set.height), (384, 1536))

    def test_rotated_text_uses_full_print_width(self) -> None:
        renderer = DocumentRenderer()
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            rotate_90_clockwise=True,
            text_columns=35,
        )
        plan = renderer.plan_text("hello\nworld", self.device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], self.device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], self.device, settings)

        self.assertEqual(preview.width, 384)
        self.assertEqual(rendered.raster_set.width, 384)

    def test_rotated_short_text_does_not_use_full_page_length(self) -> None:
        renderer = DocumentRenderer()
        settings = PrintSettings(
            dither_mode=DitherMode.NONE,
            rotate_90_clockwise=True,
            text_columns=35,
        )
        plan = renderer.plan_text("A", self.device, settings)

        preview = renderer.preview_page(plan, plan.pages[0], self.device, settings)
        rendered = renderer.print_page(plan, plan.pages[0], self.device, settings)

        self.assertEqual(preview.width, 384)
        self.assertLess(preview.height, 384)
        self.assertEqual(rendered.raster_set.width, 384)
        self.assertLess(rendered.raster_set.height, 384)

    def test_pdf_plan_keeps_source_page_count_and_selected_pages(self) -> None:
        renderer = DocumentRenderer(pdf_renderer=_FakePdfRenderer(page_count=4))
        settings = PrintSettings(pdf_pages="2-3")

        plan = renderer.plan_document(RenderDocument("content://pdf", "application/pdf"), self.device, settings)

        self.assertEqual(plan.kind, "pdf")
        self.assertEqual(plan.source_page_count, 4)
        self.assertEqual(plan.page_count, 2)
        self.assertEqual([page.source_index for page in plan.pages], [1, 2])

    def test_pdf_renders_requested_page_directly(self) -> None:
        pdf_renderer = _FakePdfRenderer(page_count=4)
        renderer = DocumentRenderer(pdf_renderer=pdf_renderer)
        settings = PrintSettings(pdf_pages="2-4")
        plan = renderer.plan_document(RenderDocument("content://pdf", "application/pdf"), self.device, settings)

        renderer.preview_page(plan, plan.pages[2], self.device, settings)

        self.assertEqual(pdf_renderer.last_document.rendered_pages, [3])

    def test_text_loader_is_used_once_during_planning(self) -> None:
        calls = []
        renderer = DocumentRenderer(text_loader=lambda source: calls.append(source) or "hello")
        document = RenderDocument("content://text", "text/plain", "note.txt")

        plan = renderer.plan_document(document, self.device, PrintSettings())
        renderer.preview_page(plan, plan.pages[0], self.device, PrintSettings())
        renderer.print_page(plan, plan.pages[0], self.device, PrintSettings())

        self.assertEqual(calls, ["content://text"])

    def test_text_is_continuous_while_images_and_pdfs_are_paged(self) -> None:
        renderer = DocumentRenderer(
            image_loader=lambda _path: _test_image(),
            pdf_renderer=_FakePdfRenderer(page_count=2),
        )
        settings = PrintSettings()

        text = renderer.plan_text("hello", self.device, settings)
        image = renderer.plan_document(RenderDocument("label.png"), self.device, settings)
        pdf = renderer.plan_document(RenderDocument("document.pdf"), self.device, settings)

        self.assertEqual(text.page_flow, PageFlow.CONTINUOUS)
        self.assertEqual(image.page_flow, PageFlow.PAGED)
        self.assertEqual(pdf.page_flow, PageFlow.PAGED)

    def _family_device(self, family: ProtocolFamily):
        return replace(
            self.device,
            protocol_family=family,
            image_pipeline=get_protocol_definition(family).behavior.default_image_pipeline,
        )


def _test_image(mode: str = "RGB") -> Image.Image:
    return Image.new(mode, (16, 2), "black")


class _RecordingImageRenderer:
    def __init__(self) -> None:
        self.preview_calls = 0
        self.raster_calls = 0
        self.preview_formats = []
        self.raster_formats = []

    def preview_image(self, img, pixel_format, *, dither_mode, gamma_handle=False, gamma_value=None):
        self.preview_calls += 1
        self.preview_formats.append(pixel_format)
        return img.convert("L")

    def raster_set(self, img, pixel_formats, *, dither_mode, gamma_handle=False, gamma_value=None):
        self.raster_calls += 1
        self.raster_formats.append(pixel_formats)
        return RasterSet.from_single(
            RasterBuffer(
                pixels=[1 for _ in range(img.width * img.height)],
                width=img.width,
                pixel_format=PixelFormat.BW1,
            )
        )


class _FakePdfRenderer:
    def __init__(self, page_count: int) -> None:
        self.page_count = page_count
        self.last_document = None

    def open(self, _path: str) -> "_FakePdfDocument":
        self.last_document = _FakePdfDocument(self.page_count)
        return self.last_document


class _FakePdfDocument:
    def __init__(self, page_count: int) -> None:
        self._page_count = page_count
        self.rendered_pages = []

    @property
    def page_count(self) -> int:
        return self._page_count

    def render_page(self, index: int, scale: float) -> Image.Image:
        self.rendered_pages.append(index)
        return _test_image()

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
