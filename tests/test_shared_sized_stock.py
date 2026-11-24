import pytest
from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.families.v5x import split_print_stream


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("name,model,paper,canvas", [
    ("QIRUI_Q1_1234", "luck_qirui_q1", "luck_label_50x15_384r_203dpi", (384, 120)),
    ("QIRUI_Q2_1234", "luck_qirui_q2", "luck_label_50x15_576r_300dpi", (576, 180)),
    ("MXW01", "v5x", "v5x_tag_50x15", (384, 120)),
    ("BH03", "funprint_v5x_gray_label", "v5x_tag_50x15", (384, 120)),
])
def test_detected_model_adds_sized_stock_without_changing_default(catalog, name, model, paper, canvas):
    device = next(device for device in catalog.detection_devices(name) if device.model_key == model)
    base = catalog.require_profile(device.profile_key)
    assert device.profile.default_paper_preset == base.default_paper_preset
    assert device.profile.paper_presets[:len(base.paper_presets)] == base.paper_presets
    assert base.paper_preset(paper) is None
    assert catalog.device_from_model(model).profile.paper_preset(paper) == device.profile.paper_preset(paper)

    settings = PrintSettings(paper_preset_key=paper, trim_side_margins=False, trim_top_bottom_margins=False)
    renderer = DocumentRenderer(image_loader=lambda path: Image.new("RGB", (40, 50), "black"))
    plan = renderer.plan_document(RenderDocument("label.png"), device, settings)
    rendered = renderer.print_page(plan, plan.pages[0], device, settings)
    assert (rendered.raster_set.width, rendered.raster_set.height) == canvas
    job = PrinterProtocol(device).build_job(
        rendered.raster_set, is_text=False, paper_preset_key=paper, image_pipeline=rendered.image_pipeline,
    )
    assert job.payload
    if paper.startswith("v5x_"):
        start = split_print_stream(job.payload).commands[-1]
        assert start[6:8] == canvas[1].to_bytes(2, "little")


def test_snap_white_label_keeps_physical_and_printable_heights_distinct(catalog):
    device = catalog.device_from_model("orgstra_s001")
    config = catalog.serialize_printer_config(device)
    rebuilt = catalog.device_from_printer_config(config)
    paper = rebuilt.profile.paper_preset("yk_snap_catalog_tag_15x30")
    assert paper.paper_mode is PaperMode.TAG
    assert (paper.render_width_px, paper.render_height_px, paper.paper_height_px) == (88, 208, 240)
    assert rebuilt.profile.default_paper_preset.key == "yk_snap_catalog_tag_14x40"
