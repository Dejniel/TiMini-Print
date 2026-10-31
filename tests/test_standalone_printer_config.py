"""Catalog and standalone printer configs share serialization, not a registry."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock, patch

import pytest

from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog, SerialTarget
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import RuntimeController


def standalone(catalog, *, preparation=None, target=None):
    device = catalog.device_from_profile("15p3", transport_target=target)
    profile = replace(device.profile, profile_key="unlisted-custom-profile")
    return replace(device.with_print_profile(profile), model_key="", origin_ids=(),
                   preparation_recipe=preparation)


@pytest.mark.parametrize("target", [
    None, SerialTarget("/dev/custom", 9600),
    BluetoothTarget(BluetoothEndpoint("Custom", "AA:BB:CC:DD:EE:01"),
                    None, "AA:BB:CC:DD:EE:01", "[classic]"),
])
def test_standalone_roundtrip_does_not_require_a_catalog_entry(target):
    catalog = PrinterCatalog.load()
    device = standalone(catalog, target=target)
    config = catalog.serialize_printer_config(device)
    assert config["schema"] == "timiniprint/printer-config/v3"
    assert config["profile"]["profile_key"] == device.profile_key
    assert config["profile_overrides"] == {} and "model_key" not in config
    with patch.object(catalog, "require_profile", side_effect=AssertionError("no catalog profile")), \
         patch.object(catalog, "device_from_model", side_effect=AssertionError("no catalog model")):
        restored = catalog.device_from_printer_config(json.loads(json.dumps(config)))
    assert restored == device
    assert catalog.serialize_printer_config(restored) == config


def test_standalone_fields_and_overrides_stay_editable():
    catalog = PrinterCatalog.load()
    config = catalog.serialize_printer_config(standalone(catalog))
    config["profile"]["dev_dpi"] = 300
    config["profile"]["paper_presets"][0]["render_width_px"] = 48
    config["profile_overrides"] = {"back_paper_num": 17}
    restored = catalog.device_from_printer_config(config)
    assert restored.profile.dev_dpi == 300
    assert restored.profile.default_paper_preset.render_width_px == 48
    assert restored.profile.back_paper_num == 17


def test_catalog_configs_use_v3_without_an_inline_profile():
    catalog = PrinterCatalog.load()
    device = catalog.device_from_model("15p3")
    config = catalog.serialize_printer_config(device)
    assert config["schema"] == "timiniprint/printer-config/v3"
    assert config["model_key"] == "15p3" and "profile" not in config
    assert "preparation_recipe" not in config["device"]
    assert catalog.device_from_printer_config(config).profile == device.profile


@pytest.mark.parametrize("change", [
    lambda c: c.update(model_key="15p3"),
    lambda c: c.update(profile_key="different"),
    lambda c: c.update(schema="timiniprint/printer-config/v1"),
    lambda c: c["profile"].pop("dev_dpi"),
    lambda c: c["device"].update(preparation_recipe=42),
    lambda c: c["device"].update(preparation_recipe=""),
    lambda c: c["device"].update(preparation_recipe=" unsupported "),
])
def test_invalid_standalone_configs_do_not_fall_back_to_a_catalog_profile(change):
    catalog = PrinterCatalog.load()
    config = catalog.serialize_printer_config(standalone(catalog))
    change(config)
    with pytest.raises((ValueError, RuntimeError)):
        catalog.device_from_printer_config(config)


def test_preparation_policy_survives_json_and_pure_profile_refinement():
    catalog = PrinterCatalog.load()
    device = standalone(catalog, preparation="future_extension")
    restored = catalog.device_from_printer_config(json.loads(json.dumps(catalog.serialize_printer_config(device))))
    assert restored.preparation_recipe == "future_extension"
    selected = catalog.device_from_model("15p3").resolve_for_connection(restored)
    assert selected.preparation_recipe == "future_extension"


@pytest.mark.parametrize("explicit", [False, True])
def test_unsupported_or_overridden_preparation_is_rejected_before_connect(explicit):
    async def run():
        catalog = PrinterCatalog.load()
        device = standalone(catalog, preparation="future_extension")
        connector = AsyncMock()
        with pytest.raises(ValueError):
            await connect_printer(device, connector, controller=RuntimeController() if explicit else None)
        connector.connect.assert_not_called()
    asyncio.run(run())
