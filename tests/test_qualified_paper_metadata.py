"""Qualified physical stock sizes remain distinct from calibrated print canvases."""

from dataclasses import replace
import json

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.catalog import MODEL_DATA_PATH, PROFILE_DATA_PATH
from timiniprint.printing.paper import resolve_paper
from timiniprint.printing.raster_job import build_raster_job
from timiniprint.printing.settings import PrintSettings
from timiniprint.raster import RasterBuffer, RasterSet


STOCKS = (
    ("luck_a2", "luck_label_50x30_384r_203dpi", (50, 30, None), (384, 380, 240, 240, 0)),
    ("luck_a40", "luck_a4_folder_a5", (148, None, "a5"), (1108, 1108, 1616, None, 0)),
    ("ibleem_v5x", "v5x_tag_40x20", (40, 20, None), (384, 320, 160, 160, 0)),
    ("xeasylabel_hp6_c", "xeasy_96_bm1_tag_14x40_36", (14, 40, None), (93, 88, 288, 304, 270)),
    ("orgstra_s001", "yk_snap_catalog_tag_15x30", (15, 30, None), (90, 88, 208, None, 270)),
    ("toprint_hprt_esc_p11", "toprint_p11_tag", (12, None, None), (88, 88, None, None, 0)),
    ("phomemo_m02", "plain_384r", (53, None, None), (384, 384, None, None, 0)),
    ("luck_a2", "plain_384r", (50, None, None), (384, 384, None, None, 0)),
)


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("model,key,nominal,canvas", STOCKS)
def test_qualified_stock_and_config_roundtrip_preserve_physical_and_print_sizes(catalog, model, key, nominal, canvas):
    device = catalog.device_from_model(model)
    paper = device.profile.paper_preset(key)
    assert (paper.nominal_width_mm, paper.nominal_height_mm, paper.standard_format) == nominal
    assert (paper.paper_width_px, paper.render_width_px, paper.render_height_px,
            paper.raster_height_px, paper.rotation_degrees) == canvas
    config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
    restored = catalog.device_from_printer_config(config)
    assert restored.profile == device.profile
    assert restored.profile.paper_preset(key) == paper


def test_shared_plain_presets_keep_source_qualified_widths_without_global_contamination(catalog):
    groups = (
        (("luck_a2", "luck_a2h", "luck_ppa2l", "luck_ppa2lh", "luck_qirui_q1", "luck_qirui_q2"), 50),
        (("phomemo_m02", "phomemo_m02s", "phomemo_m02x", "phomemo_t02", "phomemo_m02_pro"), 53),
    )
    for profiles, width in groups:
        for key in profiles:
            papers = catalog.device_from_profile(key).profile.paper_presets
            plain = next(paper for paper in papers if paper.key.startswith("plain_"))
            assert plain.nominal_width_mm == width, key
            assert plain.nominal_height_mm is None
    for key in ("d1", "x6h", "v5x", "printmaster_m_384"):
        paper = catalog.device_from_profile(key).profile.default_paper_preset
        assert paper.nominal_width_mm is paper.nominal_height_mm is paper.standard_format is None


@pytest.mark.parametrize("owner", ("profile", "model"))
@pytest.mark.parametrize("reference,error", (
    ({"nominal_width_mm": 50}, "must contain preset keys"),
    ({"key": "missing-stock"}, "unknown paper preset"),
    ({"key": "plain_384r", "render_width_px": 320}, "only physical metadata"),
    ({"key": "plain_384r", "nominal_width_mm": -1}, "nominal_width_mm"),
    ({"key": "plain_384r", "nominal_width_mm": True}, "nominal_width_mm"),
    ({"key": "plain_384r", "standard_format": ""}, "standard_format"),
))
def test_qualified_references_reject_invalid_metadata_without_allowing_geometry_changes(tmp_path, owner, reference, error):
    source = PROFILE_DATA_PATH if owner == "profile" else MODEL_DATA_PATH
    payload = json.loads(source.read_text())
    field = "paper_presets" if owner == "profile" else "additional_paper_presets"
    payload[0][field] = [reference]
    path = tmp_path / source.name
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=error):
        PrinterCatalog.load(**{f"{owner}_path": path})


@pytest.mark.parametrize("model,key,nominal,canvas", STOCKS)
def test_stock_metadata_does_not_change_layout_protocol_steps_or_completion(catalog, model, key, nominal, canvas):
    device = catalog.device_from_model(model)
    unannotated = device.with_print_profile(replace(device.profile, paper_presets=tuple(
        replace(paper, nominal_width_mm=None, nominal_height_mm=None, standard_format=None)
        for paper in device.profile.paper_presets
    )))
    settings = PrintSettings(paper_preset_key=key, image_mode="threshold", feed_padding=0)
    paper = device.profile.paper_preset(key)
    raster = RasterSet.from_single(RasterBuffer([1] + [0] * (paper.render_width_px * 2 - 1), paper.render_width_px))
    assert resolve_paper(device, settings) == resolve_paper(unannotated, settings)
    actual = build_raster_job(device, raster, is_text=False, settings=settings)
    expected = build_raster_job(unannotated, raster, is_text=False, settings=settings)
    assert actual.payload == expected.payload
    assert actual.payload_segments == expected.payload_segments
    assert actual.wait_for_completion == expected.wait_for_completion
    assert len(actual.steps) == len(expected.steps)
    for actual_step, expected_step in zip(actual.steps, expected.steps):
        assert replace(actual_step, reply_matcher=None) == replace(expected_step, reply_matcher=None)
        assert bool(actual_step.reply_matcher) == bool(expected_step.reply_matcher)
