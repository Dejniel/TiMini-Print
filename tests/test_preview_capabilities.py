"""Prepared grayscale findings apply to previews and prints identically."""
from dataclasses import asdict
from io import BytesIO

from PIL import Image
import pytest

from tests.test_rendering_document_renderer import _FakePdfRenderer
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import RuntimePrintCapabilities
from timiniprint.raster import DitherMode, PixelFormat
from timiniprint.rendering.renderer import PrintImageRenderer


@pytest.fixture(scope="module")
def device():
    return PrinterCatalog.load().device_from_profile("v5x")


@pytest.mark.parametrize("source", ["photo.png", "document.pdf", "text.txt"])
@pytest.mark.parametrize("mode", [None, ImageMode.GRAYSCALE, ImageMode.ATKINSON])
@pytest.mark.parametrize("gray", [None, False, True])
def test_preview_uses_the_print_pipeline_and_dither_without_mutating_choices(device, source, mode, gray):
    renderer = DocumentRenderer(
        image_loader=lambda _: Image.new("L", (384, 8), 100),
        text_loader=lambda _: "Preview and print",
        pdf_renderer=_FakePdfRenderer(1),
    )
    settings = PrintSettings(image_mode=mode, trim_side_margins=False, trim_top_bottom_margins=False)
    before = asdict(settings)
    capabilities = RuntimePrintCapabilities(supports_gray=gray)
    plan = renderer.plan_document(RenderDocument(source), device, settings)
    rendered = renderer.print_page(plan, plan.pages[0], device, settings,
                                    runtime_capabilities=capabilities)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings,
                                     runtime_capabilities=capabilities)
    expected_format = PixelFormat.BW1 if gray is False or mode is ImageMode.ATKINSON else PixelFormat.GRAY4
    assert rendered.image_pipeline.default_format is expected_format
    expected = PrintImageRenderer().preview_image(
        rendered.source_page.image, expected_format, dither_mode=rendered.dither_mode,
        gamma_handle=rendered.gamma_handle, gamma_value=rendered.gamma_value,
    ).convert("L")
    with Image.open(BytesIO(preview.png)) as actual:
        assert actual.size == expected.size
        assert actual.convert("L").tobytes() == expected.tobytes()
    assert rendered.dither_mode is (DitherMode.ATKINSON
        if expected_format is PixelFormat.BW1 and rendered.source_page.dither else DitherMode.NONE)
    assert (preview.raster_width, preview.raster_height) == (
        rendered.raster_set.width, rendered.raster_set.height)
    assert asdict(settings) == before


def test_omitting_capabilities_keeps_offline_preview_behavior(device):
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("L", (384, 8), 100))
    settings = PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False)
    plan = renderer.plan_document(RenderDocument("photo.png"), device, settings)
    assert renderer.preview_page(plan, plan.pages[0], device, settings) == renderer.preview_page(
        plan, plan.pages[0], device, settings, runtime_capabilities=None)
