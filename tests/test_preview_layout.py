"""Preview placement is descriptive; it must not change the printable body."""
from dataclasses import FrozenInstanceError, replace
from io import BytesIO

import pytest
from PIL import Image

from timiniprint.devices import PaperPreset, PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat


@pytest.fixture(scope="module")
def base_device():
    return PrinterCatalog.load().device_from_profile("x6h")


@pytest.fixture
def page_setup(base_device):
    def setup(paper, image=None):
        device = base_device.with_print_profile(replace(
            base_device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key,
        ))
        renderer = DocumentRenderer(image_loader=lambda _: image if image is not None
                                    else Image.new("RGB", (8, 2), "black"))
        settings = PrintSettings(image_mode=ImageMode.THRESHOLD,
                                 trim_side_margins=False, trim_top_bottom_margins=False)
        plan = renderer.plan_document(RenderDocument("test.png"), device, settings)
        return renderer, plan, device, settings
    return setup


@pytest.mark.parametrize("origin,body_width,raster_left", [(None, 16, 0), (0, 8, 0), (4, 8, 4)])
def test_preview_shows_the_declared_canvas_without_adding_padding_to_print(page_setup, origin, body_width, raster_left):
    paper = PaperPreset("test", "Test", 16, 8, left_padding_px=origin)
    renderer, plan, device, settings = page_setup(paper)
    protocol = PrinterProtocol(device)
    before = protocol.build_job(renderer.print_page(plan, plan.pages[0], device, settings).raster_set, is_text=False)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    printed = renderer.print_page(plan, plan.pages[0], device, settings).raster_set
    after = protocol.build_job(printed, is_text=False)

    assert preview.width == 16 and preview.height == 2
    assert preview.raster_width == printed.width == body_width
    assert before.payload == after.payload and before.steps == after.steps
    layout = preview.layout
    assert (layout.width, layout.height) == (16, 2)
    assert (layout.raster.left, layout.raster.top, layout.raster.width, layout.raster.height) == (
        raster_left, 0, body_width, 2,
    )
    left = 4 if origin is None else origin
    assert (layout.content.left, layout.content.top, layout.content.width, layout.content.height) == (left, 0, 8, 2)
    assert (layout.left_margin_px, layout.top_margin_px, layout.right_margin_px, layout.bottom_margin_px) == (
        left, 0, 8 - left, 0,
    )
    with Image.open(BytesIO(preview.png)) as image:
        assert list(image.convert("L").getdata())[:16] == [255] * left + [0] * 8 + [255] * (8 - left)
    if origin is not None:
        assert list(printed.require(PixelFormat.BW1).pixels) == [1] * 16
    with pytest.raises(FrozenInstanceError):
        layout.content.left = 9


@pytest.mark.parametrize("mirror,content_left", [(False, 5), (True, 10)])
def test_preview_geometry_follows_fitting_top_padding_and_body_mirroring(page_setup, mirror, content_left):
    paper = PaperPreset("test", "Test", 16, 8, left_padding_px=3, render_height_px=2,
                        top_padding_px=1, raster_height_px=6, raster_width_px=16,
                        mirror_horizontal=mirror)
    renderer, plan, device, settings = page_setup(paper, Image.new("RGB", (8, 4), "black"))
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    printed = renderer.print_page(plan, plan.pages[0], device, settings).raster_set
    assert (preview.width, preview.height) == (16, 6)
    assert (printed.width, printed.height) == (13, 6)
    assert (preview.layout.raster.left, preview.layout.raster.width) == (3, 13)
    assert (preview.layout.content.left, preview.layout.content.top,
            preview.layout.content.width, preview.layout.content.height) == (content_left, 1, 4, 2)
    assert (preview.layout.top_margin_px, preview.layout.bottom_margin_px) == (1, 3)
    with Image.open(BytesIO(preview.png)) as image:
        assert image.getpixel((content_left, 1)) == 0
        assert image.getpixel((content_left - 1, 1)) == 255
        assert image.getpixel((content_left + 4, 1)) == 255


def test_exact_raster_canvas_is_not_expanded_to_a_larger_protocol_width(page_setup):
    paper = PaperPreset("test", "Test", 20, 8, left_padding_px=4, raster_width_px=12)
    renderer, plan, device, settings = page_setup(paper)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    assert preview.width == preview.layout.width == 12
    assert preview.raster_width == 8
    assert (preview.layout.left_margin_px, preview.layout.right_margin_px) == (4, 0)


def test_content_rectangle_does_not_depend_on_which_pixels_are_white(page_setup):
    paper = PaperPreset("test", "Test", 16, 8, left_padding_px=4)
    renderer, plan, device, settings = page_setup(paper, Image.new("RGB", (8, 2), "white"))
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    assert (preview.layout.content.left, preview.layout.content.width) == (4, 8)
    assert (preview.layout.left_margin_px, preview.layout.right_margin_px) == (4, 4)


def test_source_preview_has_no_printer_geometry():
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (8, 2), "red"))
    preview = renderer.preview_source_page(RenderDocument("test.png"), target_width=8)
    assert preview.layout is None
    assert (preview.width, preview.height) == (8, 2)
    with Image.open(BytesIO(preview.png)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
