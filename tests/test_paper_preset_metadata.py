"""Physical stock metadata is optional and never controls print geometry."""
import asyncio
from dataclasses import replace
import json

import pytest
from PIL import Image

from timiniprint.devices import PaperPreset, PrinterCatalog
from timiniprint.devices.catalog import PAPER_PRESET_DATA_PATH
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.paper import paper_presets_for_device, resolve_paper
from timiniprint.printing.raster_job import build_raster_job
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import PaperMode
from timiniprint.raster import RasterBuffer, RasterSet


METADATA = (
    {"nominal_width_mm": 50, "nominal_height_mm": 30},
    {"nominal_width_mm": 58, "nominal_height_mm": None},
    {"nominal_width_mm": 210, "nominal_height_mm": 297, "standard_format": "a4"},
)
METADATA_FIELDS = ("nominal_width_mm", "nominal_height_mm", "standard_format")


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def _metadata(paper):
    return {field: getattr(paper, field) for field in METADATA_FIELDS}


@pytest.mark.parametrize("metadata", METADATA)
def test_metadata_json_roundtrip(metadata):
    payload = dict(key="stock", label="Stock", paper_width_px=384, render_width_px=384, **metadata)
    paper = model_from_json(PaperPreset, payload)
    assert _metadata(paper) == {field: metadata.get(field) for field in METADATA_FIELDS}
    assert type(paper.nominal_width_mm) is float
    assert model_from_json(PaperPreset, json.loads(json.dumps(model_to_json(paper)))) == paper


def test_unspecified_metadata_is_not_inferred_from_geometry_or_format():
    paper = PaperPreset("sheet", "Sheet", 1600, 1600, render_height_px=2338)
    assert all(value is None for value in _metadata(paper).values())
    named = replace(paper, standard_format="a4")
    assert named.nominal_width_mm is named.nominal_height_mm is None


@pytest.mark.parametrize("field", ("nominal_width_mm", "nominal_height_mm"))
@pytest.mark.parametrize("value", (0, -1, float("nan"), float("inf"), True, "58", []))
@pytest.mark.parametrize("from_json", (False, True))
def test_nominal_dimensions_reject_invalid_values(field, value, from_json):
    payload = dict(key="stock", label="Stock", paper_width_px=384, render_width_px=384, **{field: value})
    with pytest.raises(ValueError, match=field):
        model_from_json(PaperPreset, payload) if from_json else PaperPreset(**payload)


@pytest.mark.parametrize("value", ("", "  ", True, 4))
def test_standard_format_requires_a_nonempty_string(value):
    with pytest.raises(ValueError, match="standard_format"):
        PaperPreset("stock", "Stock", 384, 384, standard_format=value)


def test_catalog_json_exposes_metadata_without_changing_existing_geometry(catalog, tmp_path):
    payload = json.loads(PAPER_PRESET_DATA_PATH.read_text(encoding="utf-8"))
    original = catalog.device_from_profile("d1").profile.default_paper_preset
    before = dict(payload[original.key])
    payload[original.key].update(METADATA[1])
    path = tmp_path / "paper_presets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = PrinterCatalog.load(paper_preset_path=path)
    paper = loaded.device_from_profile("d1").profile.default_paper_preset
    assert paper == replace(original, **METADATA[1])
    assert paper.key == original.key and paper.label == before["label"]
    assert paper.nominal_height_mm is None


@pytest.mark.parametrize("selection", ("model", "profile", "standalone"))
@pytest.mark.parametrize("metadata", (*METADATA, {}))
def test_printer_configs_preserve_supplied_metadata(catalog, selection, metadata):
    device = (catalog.device_from_model("15p3") if selection == "model"
              else catalog.device_from_profile("15p3"))
    paper = replace(device.profile.default_paper_preset, **metadata)
    profile = replace(device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key)
    if selection == "standalone":
        profile = replace(profile, profile_key="custom-stock")
    device = device.with_print_profile(profile)
    config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
    stored = config["profile" if selection == "standalone" else "profile_overrides"]["paper_presets"][0]
    for field in METADATA_FIELDS:
        if getattr(paper, field) is None:
            assert field not in stored
        else:
            assert stored[field] == getattr(paper, field)
    rebuilt = catalog.device_from_printer_config(config)
    assert rebuilt.profile == device.profile
    assert paper_presets_for_device(rebuilt) == (paper,)
    assert catalog.serialize_printer_config(rebuilt) == config


@pytest.mark.parametrize("profile_key", ("d1", "x6h", "a43"))
@pytest.mark.parametrize("metadata", METADATA)
def test_metadata_does_not_change_resolved_paper_or_print_job(catalog, profile_key, metadata):
    device = catalog.device_from_profile(profile_key)
    annotated = device.with_print_profile(replace(device.profile, paper_presets=tuple(
        replace(paper, **metadata) for paper in device.profile.paper_presets
    )))
    settings = PrintSettings(feed_padding=0)
    raster = RasterSet.from_single(RasterBuffer([0, 1] * 8, width=8))
    assert resolve_paper(annotated, settings) == resolve_paper(device, settings)
    actual = build_raster_job(annotated, raster, is_text=False, settings=settings)
    expected = build_raster_job(device, raster, is_text=False, settings=settings)
    assert actual.payload == expected.payload
    assert actual.payload_segments == expected.payload_segments
    assert len(actual.steps) == len(expected.steps)
    for actual_step, expected_step in zip(actual.steps, expected.steps):
        assert replace(actual_step, reply_matcher=None) == replace(expected_step, reply_matcher=None)
        assert bool(actual_step.reply_matcher) == bool(expected_step.reply_matcher)
    assert actual.wait_for_completion == expected.wait_for_completion


@pytest.mark.parametrize("profile_key", ("d1", "x6h", "a43"))
def test_metadata_does_not_change_file_raster_or_preview(catalog, profile_key):
    device = catalog.device_from_profile(profile_key)
    annotated = device.with_print_profile(replace(device.profile, paper_presets=tuple(
        replace(paper, **METADATA[2]) for paper in device.profile.paper_presets
    )))
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (32, 1), "black"))
    document = RenderDocument("image.png")
    settings = PrintSettings(image_mode=ImageMode.ATKINSON)
    plan = renderer.plan_document(document, device, settings)
    annotated_plan = renderer.plan_document(document, annotated, settings)
    assert annotated_plan == plan
    assert renderer.print_page(annotated_plan, annotated_plan.pages[0], annotated, settings).raster_set == renderer.print_page(
        plan, plan.pages[0], device, settings,
    ).raster_set
    assert renderer.preview_page(annotated_plan, annotated_plan.pages[0], annotated, settings) == renderer.preview_page(
        plan, plan.pages[0], device, settings,
    )


def _prepare(original, resolved):
    class Controller(RuntimeController):
        async def prepare(self, device, session, *, timeout):
            return PreparedPrinter(resolved, self)

    class Connection:
        async def attach_runtime_controller(self, controller, *, timeout):
            pass

    return asyncio.run(prepare_connection_runtime(original, Connection(), controller=Controller())).device


@pytest.mark.parametrize("rename", (False, True))
def test_negotiation_preserves_stock_metadata_independently_of_dots(catalog, rename):
    device = catalog.device_from_profile("d1")
    source = replace(device.profile.default_paper_preset, paper_mode=PaperMode.PLAIN, **METADATA[2])
    device = device.with_print_profile(replace(device.profile, paper_presets=(source,), default_paper_preset_key=source.key))
    # Reconstructed preset: different head/DPI, no copied presentation fields.
    paper = PaperPreset("negotiated" if rename else source.key, "Resolved", 576, 576, paper_mode=source.paper_mode)
    resolved = device.with_print_profile(replace(device.profile, dev_dpi=300, paper_presets=(paper,), default_paper_preset_key=paper.key))
    prepared = _prepare(device, resolved)
    assert _metadata(prepared.profile.default_paper_preset) == _metadata(source)
    assert prepared.profile.dev_dpi == 300
    assert prepared.profile.default_paper_preset.paper_width_px == 576


@pytest.mark.parametrize("case", ("other_mode", "same_key_other_mode", "ambiguous", "ambiguous_source", "new_profile", "detected_size"))
def test_negotiation_does_not_assign_metadata_to_unrelated_stock(catalog, case):
    device = catalog.device_from_profile("d1")
    source = replace(device.profile.default_paper_preset, paper_mode=PaperMode.PLAIN, **METADATA[2])
    device = device.with_print_profile(replace(device.profile, paper_presets=(source,), default_paper_preset_key=source.key))
    paper = PaperPreset("detected", "Detected", 576, 576, paper_mode=source.paper_mode)
    papers = (paper,)
    if case in ("other_mode", "same_key_other_mode"):
        paper = replace(paper, paper_mode=PaperMode.TAG)
        if case == "same_key_other_mode":
            paper = replace(paper, key=source.key)
        papers = (paper,)
    elif case == "ambiguous":
        papers = (paper, replace(paper, key="other-stock"))
    elif case == "detected_size":
        paper = replace(paper, key=source.key, nominal_width_mm=58)
        papers = (paper,)
    elif case == "ambiguous_source":
        device = device.with_print_profile(replace(device.profile, paper_presets=(source, replace(source, key="alternate"))))
    profile = replace(device.profile, paper_presets=papers, default_paper_preset_key=paper.key)
    if case == "new_profile":
        profile = replace(profile, profile_key="other-profile")
    prepared = _prepare(device, device.with_print_profile(profile))
    assert _metadata(prepared.profile.default_paper_preset) == _metadata(paper)


def test_generic_stock_metadata_follows_only_the_negotiated_default(catalog):
    device = catalog.device_from_profile("d1")
    source = replace(device.profile.default_paper_preset, paper_mode=None, **METADATA[0])
    device = device.with_print_profile(replace(device.profile, paper_presets=(source,), default_paper_preset_key=source.key))
    plain = PaperPreset("plain", "Plain", 576, 576, paper_mode=PaperMode.PLAIN)
    label = replace(plain, key="label", paper_mode=PaperMode.TAG)
    resolved = device.with_print_profile(replace(device.profile, paper_presets=(plain, label), default_paper_preset_key=label.key))
    prepared = _prepare(device, resolved)
    assert _metadata(prepared.profile.default_paper_preset) == _metadata(source)
    assert all(value is None for value in _metadata(prepared.profile.paper_preset(plain.key)).values())
