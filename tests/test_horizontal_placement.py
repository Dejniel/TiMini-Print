"""Narrow content can retain a tested wire width without moving its origin."""
from dataclasses import replace
import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from timiniprint.devices import PaperPreset, PrinterCatalog
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
from timiniprint.printing.paper import ResolvedPaper, apply_paper_layout_to_raster_set
from timiniprint.printing.settings import PrintSettings
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.mark.parametrize("origin,prefix", [(None, 4), (0, 0)])
def test_raw_content_alignment_preserves_protocol_and_raster_width(origin, prefix):
    paper = ResolvedPaper("test", "Test", None, 16, 8, raster_width_px=16, left_padding_px=origin)
    source = RasterSet.from_single(RasterBuffer([1] * 8, 8))
    result = apply_paper_layout_to_raster_set(source, paper)
    assert result.width == 16
    assert list(result.require(PixelFormat.BW1).pixels) == [0] * prefix + [1] * 8 + [0] * (8 - prefix)


def test_left_alignment_matches_document_preview_and_print_without_changing_wire_width():
    device = PrinterCatalog.load().device_from_profile("x6h")
    paper = PaperPreset("test", "Test", 16, 8, raster_width_px=16, left_padding_px=0)
    device = device.with_print_profile(replace(device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key))
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (8, 2), "black"))
    settings = PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False)
    plan = renderer.plan_document(RenderDocument("test.png"), device, settings)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    printed = renderer.print_page(plan, plan.pages[0], device, settings).raster_set
    assert preview.width == printed.width == 16
    assert list(Image.open(BytesIO(preview.png)).convert("L").getdata())[:16] == [0] * 8 + [255] * 8
    assert list(printed.require(PixelFormat.BW1).pixels)[:16] == [1] * 8 + [0] * 8


def test_alignment_roundtrip_and_omitted_field_keep_the_old_default():
    paper = PaperPreset("test", "Test", 384, 320, left_padding_px=0, raster_width_px=384)
    payload = json.loads(json.dumps(model_to_json(paper)))
    assert model_from_json(PaperPreset, payload) == paper
    del payload["left_padding_px"]
    assert model_from_json(PaperPreset, payload).left_padding_px is None
    with pytest.raises(ValueError):
        replace(paper, left_padding_px="invalid")


@pytest.mark.parametrize("origin,content,new_width,expected", [
    (None, 320, 640, 320),
    (None, 384, 640, 640),
    (0, 384, 640, 640),
    (0, 320, 640, 320),
    (0, 320, 256, 256),
])
def test_preparation_keeps_explicit_content_box_but_negotiates_wire_bounds(origin, content, new_width, expected):
    class Resize(RuntimeController):
        async def prepare(self, device, session, *, timeout):
            paper = PaperPreset("negotiated", "Negotiated", new_width, new_width)
            profile = replace(device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key)
            return PreparedPrinter(device.with_print_profile(profile), self)

    class Connection:
        async def disconnect(self): pass

    class Connector:
        async def connect(self, device): return Connection()

    async def run():
        device = PrinterCatalog.load().device_from_profile("x6h")
        paper = PaperPreset("chosen", "Chosen", 384, content, left_padding_px=origin,
                            raster_width_px=384 if origin == 0 else None)
        device = device.with_print_profile(replace(device.profile, paper_presets=(paper,),
                                                   default_paper_preset_key=paper.key))
        async with await connect_printer(device, Connector(), controller=Resize()) as printer:
            actual = printer.printer_device().profile.default_paper_preset
            assert actual.paper_width_px == new_width
            assert actual.render_width_px == expected
            assert actual.left_padding_px == origin
            if origin == 0:
                assert actual.raster_width_px == new_width
    asyncio.run(run())



@pytest.mark.parametrize("profile,encoding,fmt,ink,white", [
    ("v5x", "v5x_dot", PixelFormat.BW1, 1, 0),
    ("ytb01", "v5c_a4", PixelFormat.BW1, 1, 0),
    ("v5x", "v5x_gray", PixelFormat.GRAY4, 15, 0),
    ("v5x", "v5x_gray", PixelFormat.GRAY8, 0, 255),
])
def test_generic_protocol_offset_matches_a_once_padded_raster(profile, encoding, fmt, ink, white):
    from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PrinterProtocol

    device = PrinterCatalog.load().device_from_profile(profile)
    paper = PaperPreset("test", "Test", 16, 8, left_padding_px=8, raster_width_px=16)
    shifted = device.with_print_profile(replace(
        device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key,
    ))
    manual = shifted.with_print_profile(replace(
        shifted.profile, paper_presets=(replace(paper, left_padding_px=0, render_width_px=16),),
    ))
    pipeline = ImagePipelineConfig(formats=(fmt,), encoding=ImageEncoding(encoding))
    source = RasterSet.from_single(RasterBuffer([ink] * 8, 8, fmt))
    padded = RasterSet.from_single(RasterBuffer([white] * 8 + [ink] * 8, 16, fmt))
    actual = PrinterProtocol(shifted).build_job(source, is_text=False, image_pipeline=pipeline)
    expected = PrinterProtocol(manual).build_job(padded, is_text=False, image_pipeline=pipeline)
    assert actual.payload == expected.payload
    assert actual.steps == expected.steps


@pytest.mark.parametrize("negotiate", [False, True])
def test_preparation_preserves_each_papers_constraints_without_spreading_the_default(negotiate):
    class Resize(RuntimeController):
        async def prepare(self, device, session, *, timeout):
            if not negotiate:
                return PreparedPrinter(device, self)
            papers = (PaperPreset("small", "Small", 640, 640),
                      PaperPreset("large", "Large", 1024, 1024))
            return PreparedPrinter(device.with_print_profile(replace(device.profile, paper_presets=papers)), self)

    class Connection:
        async def disconnect(self): pass

    class Connector:
        async def connect(self, device): return Connection()

    async def run():
        device = PrinterCatalog.load().device_from_profile("x6h")
        papers = (PaperPreset("small", "Small", 384, 320, left_padding_px=0, raster_width_px=384),
                  PaperPreset("large", "Large", 768, 768))
        device = device.with_print_profile(replace(
            device.profile, paper_presets=papers, default_paper_preset_key="small",
        ))
        async with await connect_printer(device, Connector(), controller=Resize()) as printer:
            actual = printer.printer_device().profile.paper_presets
            if negotiate:
                assert actual[0].render_width_px == 320 and actual[0].raster_width_px == 640
                assert actual[1].render_width_px == 1024 and actual[1].left_padding_px is None
            else:
                assert actual == papers
    asyncio.run(run())
