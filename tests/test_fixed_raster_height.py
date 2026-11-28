"""Prepared pages cannot bypass an existing preset's exact raster height."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.paper import apply_paper_layout_to_raster_set, resolve_paper
from timiniprint.printing.raster_job import build_raster_page_job
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


FIXED_PAPERS = (("v5x", "v5x_tag_50x15"),
                ("luck_qirui_q1", "luck_label_50x15_384r_203dpi"))


def raster(width, height):
    return RasterSet.from_single(RasterBuffer(bytes([1]) + bytes(width * height - 1), width, PixelFormat.BW1))


def selected_paper(catalog, model, key):
    device = catalog.device_from_model(model)
    return device.with_print_profile(replace(device.profile, default_paper_preset_key=key))


@pytest.mark.parametrize("model,key", FIXED_PAPERS)
@pytest.mark.parametrize("placed", [False, True])
def test_fixed_height_overflow_is_rejected_before_encoding(catalog, monkeypatch, model, key, placed):
    device = selected_paper(catalog, model, key)
    settings = PrintSettings(image_mode="threshold")
    paper = resolve_paper(device, settings)
    pixels = raster(paper.paper_width_px, paper.raster_height_px + 1)
    encode = Mock(side_effect=AssertionError("Overflow must fail before protocol encoding"))
    monkeypatch.setattr(PrinterProtocol, "build_job", encode)
    with pytest.raises(ValueError, match="exceeds paper raster height"):
        build_raster_page_job(device, pixels, is_text=False, settings=settings, _paper_layout_applied=placed)
    encode.assert_not_called()


@pytest.mark.parametrize("model,key", FIXED_PAPERS)
@pytest.mark.parametrize("placed", [False, True])
@pytest.mark.parametrize("shorter", [False, True])
def test_fitting_rasters_keep_their_existing_layout_and_wire_bytes(catalog, model, key, placed, shorter):
    device = selected_paper(catalog, model, key)
    settings = PrintSettings(image_mode="threshold")
    paper = resolve_paper(device, settings)
    pixels = raster(paper.paper_width_px, paper.raster_height_px - int(shorter))
    prepared = pixels if placed else apply_paper_layout_to_raster_set(pixels, paper)
    pipeline = settings.resolve_image_pipeline(device, raster_formats=(PixelFormat.BW1,))
    expected = PrinterProtocol(device).build_job(
        prepared, is_text=False, blackening=settings.blackening, feed_padding=12,
        paper_mode=paper.paper_mode, image_pipeline=pipeline,
    )
    actual = build_raster_page_job(device, pixels, is_text=False, settings=settings, _paper_layout_applied=placed)
    assert (actual.payload, actual.steps, actual.wait_for_completion) == (
        expected.payload, expected.steps, expected.wait_for_completion,
    )


@pytest.mark.parametrize("model,_", FIXED_PAPERS)
def test_continuous_media_does_not_acquire_a_fixed_height_limit(catalog, model, _):
    device = selected_paper(catalog, model, "plain_384r")
    settings = PrintSettings(image_mode="threshold")
    paper = resolve_paper(device, settings)
    assert paper.raster_height_px is None
    pixels = raster(paper.paper_width_px, 1000)
    raw = build_raster_page_job(device, pixels, is_text=False, settings=settings)
    placed = build_raster_page_job(device, pixels, is_text=False, settings=settings, _paper_layout_applied=True)
    assert (raw.payload, raw.steps) == (placed.payload, placed.steps)


@pytest.mark.parametrize("streaming", [False, True])
def test_hp6_c_merged_text_obeys_the_selected_label_height(catalog, monkeypatch, streaming):
    device = selected_paper(catalog, "xeasylabel_hp6_c", "xeasy_96_bm1_tag_14x22")
    settings = PrintSettings(image_mode="threshold")
    renderer = DocumentRenderer(text_loader=lambda _: "line\n" * 100)
    plan = renderer.plan_document(RenderDocument("memory.txt"), device, settings)
    assert plan.page_count > 1
    assert PrinterProtocol(device).requires_contiguous_raster_for_continuous_pages()
    builder = PrintJobBuilder(device, settings, document_renderer=renderer)
    monkeypatch.setattr("timiniprint.printing.builder.os.path.isfile", lambda _: True)
    encode = Mock(side_effect=AssertionError("Merged label overflow must fail before protocol encoding"))
    monkeypatch.setattr(PrinterProtocol, "build_job", encode)
    with pytest.raises(ValueError, match="exceeds paper raster height"):
        if streaming:
            list(builder.iter_page_jobs("memory.txt"))
        else:
            builder.build_from_file("memory.txt")
    encode.assert_not_called()
