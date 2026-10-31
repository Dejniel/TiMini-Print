"""Only current printer configs are accepted, without geometry reinterpretation."""
import copy
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from timiniprint.devices import PaperPreset, PrinterCatalog


@pytest.mark.parametrize("selection", ["model", "profile", "standalone"])
@pytest.mark.parametrize("origin", [None, 0, 4])
def test_config_preserves_explicit_geometry_and_does_not_mutate_input(selection, origin):
    catalog = PrinterCatalog.load()
    device = (catalog.device_from_model("15p3") if selection == "model"
              else catalog.device_from_profile("15p3"))
    paper = PaperPreset("paper", "Paper", 16, 8, left_padding_px=origin, raster_width_px=16)
    profile = replace(device.profile, paper_presets=(paper,), default_paper_preset_key=paper.key)
    if selection == "standalone":
        profile = replace(profile, profile_key="custom-layout")
    device = device.with_print_profile(profile)
    config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
    before = copy.deepcopy(config)
    restored = catalog.device_from_printer_config(config)
    assert config["schema"] == "timiniprint/printer-config/v3"
    assert restored.profile.default_paper_preset == paper
    assert config == before
    assert catalog.serialize_printer_config(restored) == config


@pytest.mark.parametrize("selection", ["model", "profile", "standalone"])
@pytest.mark.parametrize("schema", ["timiniprint/printer-config/v1", "timiniprint/printer-config/v2",
                                    "timiniprint/printer-config/v4", "", None])
def test_other_schemas_are_rejected_before_catalog_lookup(selection, schema):
    catalog = PrinterCatalog.load()
    device = (catalog.device_from_model("15p3") if selection == "model"
              else catalog.device_from_profile("15p3"))
    if selection == "standalone":
        device = device.with_print_profile(replace(device.profile, profile_key="custom-layout"))
    config = catalog.serialize_printer_config(device)
    config["schema"] = schema
    before = copy.deepcopy(config)
    with patch.object(catalog, "require_profile", side_effect=AssertionError("no lookup")), \
         patch.object(catalog, "device_from_model", side_effect=AssertionError("no lookup")):
        with pytest.raises(RuntimeError, match="Unsupported printer config schema"):
            catalog.device_from_printer_config(config)
    assert config == before


def test_removed_alignment_field_is_not_translated():
    catalog = PrinterCatalog.load()
    config = catalog.serialize_printer_config(catalog.device_from_profile("15p3"))
    config["profile_overrides"]["paper_presets"][0]["horizontal_alignment"] = "left"
    with pytest.raises(ValueError, match="horizontal_alignment"):
        catalog.device_from_printer_config(config)
