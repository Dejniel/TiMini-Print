"""Public config migration keeps legacy geometry out of rendering policy."""
from dataclasses import replace
import copy
import json

import pytest

from timiniprint.devices import PaperPreset, PrinterCatalog


def legacy(schema, *, origin=0, row=None, profile="x6h"):
    catalog = PrinterCatalog.load()
    device = catalog.device_from_profile(profile)
    if schema.endswith("/v2"):
        device = device.with_print_profile(replace(device.profile, profile_key="custom-layout"))
    config = catalog.serialize_printer_config(device)
    config["schema"] = schema
    section = config["profile"] if "profile" in config else config["profile_overrides"]
    section["paper_presets"] = [dict(key="paper", label="Paper", paper_width_px=16,
                                    render_width_px=8, left_padding_px=origin, raster_width_px=row)]
    section["default_paper_preset_key"] = "paper"
    return config, section


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_zero_is_centering_and_import_does_not_mutate_the_snapshot(version):
    catalog = PrinterCatalog.load()
    config, _ = legacy(f"timiniprint/printer-config/v{version}")
    before = copy.deepcopy(config)
    device = catalog.device_from_printer_config(config)
    assert device.profile.default_paper_preset.left_padding_px is None
    assert config == before
    saved = catalog.serialize_printer_config(device)
    assert saved["schema"] == "timiniprint/printer-config/v3"
    assert catalog.device_from_printer_config(json.loads(json.dumps(saved))).profile == device.profile


@pytest.mark.parametrize("origin", [None, 0, 4])
def test_v3_preserves_null_zero_and_positive_origin_without_reinterpreting_them(origin):
    catalog = PrinterCatalog.load()
    device = catalog.device_from_profile("x6h")
    paper = PaperPreset("paper", "Paper", 16, 8, left_padding_px=origin, raster_width_px=16)
    device = device.with_print_profile(replace(device.profile, profile_key="custom-layout",
                                              paper_presets=(paper,), default_paper_preset_key=paper.key))
    config = catalog.serialize_printer_config(device)
    restored = catalog.device_from_printer_config(json.loads(json.dumps(config)))
    assert config["schema"] == "timiniprint/printer-config/v3"
    assert restored.profile.default_paper_preset == paper
    assert "horizontal_alignment" not in json.dumps(config)


def test_legacy_encoder_offset_retains_origin_and_converts_body_width_to_full_row():
    catalog = PrinterCatalog.load()
    config, _ = legacy("timiniprint/printer-config/v2", origin=8, row=8)
    paper = catalog.device_from_printer_config(config).profile.default_paper_preset
    assert paper.left_padding_px == 8 and paper.raster_width_px == 16


def test_legacy_offset_used_only_to_suppress_centering_becomes_explicit_raster_geometry():
    catalog = PrinterCatalog.load()
    config, _ = legacy("timiniprint/printer-config/v2", origin=8, profile="v5x")
    paper = catalog.device_from_printer_config(config).profile.default_paper_preset
    assert paper.left_padding_px == 0 and paper.raster_width_px == 8


def test_previous_alignment_field_is_translated_only_at_the_old_config_boundary():
    catalog = PrinterCatalog.load()
    config, section = legacy("timiniprint/printer-config/v2")
    section["paper_presets"][0]["horizontal_alignment"] = "left"
    paper = catalog.device_from_printer_config(config).profile.default_paper_preset
    assert paper.left_padding_px == 0 and paper.raster_width_px == 16
    config["schema"] = "timiniprint/printer-config/v3"
    with pytest.raises(ValueError, match="horizontal_alignment"):
        catalog.device_from_printer_config(config)


@pytest.mark.parametrize("origin", [-1, True, "0", 1.5])
def test_bad_legacy_offsets_are_not_silently_normalized(origin):
    config, _ = legacy("timiniprint/printer-config/v2", origin=origin)
    with pytest.raises(ValueError, match="left_padding_px"):
        PrinterCatalog.load().device_from_printer_config(config)


@pytest.mark.parametrize("row", [-1, 0, True, "8", 8.5])
def test_bad_legacy_row_widths_are_not_coerced_during_migration(row):
    config, _ = legacy("timiniprint/printer-config/v2", origin=8, row=row)
    with pytest.raises(ValueError, match="raster_width_px"):
        PrinterCatalog.load().device_from_printer_config(config)
