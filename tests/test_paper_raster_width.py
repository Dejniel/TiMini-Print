from dataclasses import replace
import json

import pytest
from PIL import Image

from timiniprint.devices import PaperPreset, PrinterCatalog
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.paper import ResolvedPaper, apply_paper_layout_to_raster_set, resolve_paper
from timiniprint.printing.settings import PrintSettings
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def test_final_raster_width_is_independent_of_protocol_width_in_preview_and_print():
    device = PrinterCatalog.load().device_from_profile("x6h")
    preset = replace(device.profile.default_paper_preset,
                     paper_width_px=400, render_width_px=384, raster_width_px=384)
    device = replace(device, profile=replace(device.profile, paper_presets=(preset,)))
    settings = PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False)
    renderer = DocumentRenderer(image_loader=lambda path: Image.new("RGB", (384, 20), "black"))
    plan = renderer.plan_document(RenderDocument("label.png"), device, settings)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    rendered = renderer.print_page(plan, plan.pages[0], device, settings)
    assert preview.width == rendered.raster_set.width == 384
    assert resolve_paper(device, settings).paper_width_px == 400


@pytest.mark.parametrize("pixel_format,black,white", [
    (PixelFormat.BW1, 1, 0), (PixelFormat.GRAY8, 0, 255),
])
def test_explicit_width_pads_short_raw_rasters_without_padding_to_protocol_width(pixel_format, black, white):
    paper = ResolvedPaper(key="narrow", label="Narrow", paper_mode=None,
                          paper_width_px=16, render_width_px=4, raster_width_px=8)
    raster = RasterSet.from_single(RasterBuffer([black] * 4, 4, pixel_format))
    padded = apply_paper_layout_to_raster_set(raster, paper)
    assert padded.width == 8
    assert list(padded.require(pixel_format).pixels) == [white] * 2 + [black] * 4 + [white] * 2


def test_oversized_raw_raster_is_rejected_not_cropped():
    paper = ResolvedPaper(key="narrow", label="Narrow", paper_mode=None,
                          paper_width_px=16, render_width_px=4, raster_width_px=8)
    raster = RasterSet.from_single(RasterBuffer([1] * 9, 9, PixelFormat.BW1))
    with pytest.raises(ValueError, match="exceeds paper raster width"):
        apply_paper_layout_to_raster_set(raster, paper)


def test_preset_width_is_validated_and_round_trips_through_json():
    preset = PaperPreset("narrow", "Narrow", 400, 384, raster_width_px=384)
    restored = model_from_json(PaperPreset, json.loads(json.dumps(model_to_json(preset))))
    assert restored == preset
    assert replace(preset, raster_width_px=None).raster_width_px is None
    for width in (-1, 0, 383):
        with pytest.raises(ValueError, match="raster_width_px"):
            replace(preset, raster_width_px=width)


def test_omitted_width_retains_protocol_canvas_padding():
    paper = ResolvedPaper(key="legacy", label="Legacy", paper_mode=None,
                          paper_width_px=16, render_width_px=8)
    raster = RasterSet.from_single(RasterBuffer([1] * 8, 8, PixelFormat.BW1))
    assert apply_paper_layout_to_raster_set(raster, paper).width == 16
