from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import PrintSettings
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


def _test_image() -> Image.Image:
    return Image.new("RGB", (16, 2), "black")


class _RecordingImageRenderer:
    def __init__(self) -> None:
        self.preview_calls = 0
        self.raster_calls = 0

    def transform_page(self, page, rotate_90_clockwise=False):
        return page

    def preview_image(self, img, pixel_format, *, dither_mode, gamma_handle=False, gamma_value=None):
        self.preview_calls += 1
        return img.convert("L")

    def raster_set(self, img, pixel_formats, *, dither_mode, gamma_handle=False, gamma_value=None):
        self.raster_calls += 1
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
