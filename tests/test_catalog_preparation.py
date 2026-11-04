from dataclasses import replace
import json

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import ModelDetection, PrinterModel


def configured_catalog():
    profile = PrinterCatalog.load().require_profile("v5x")
    model = PrinterModel(
        model_key="configured", profile_key=profile.profile_key, origin_ids=("source",),
        detections=(ModelDetection(exact_names=("Demo",)),),
        preparation_recipe="session-policy",
    )
    return PrinterCatalog((profile,), (model,), origin_names={"source": "Source"})


def test_model_preparation_is_preserved_in_detected_and_manual_devices():
    catalog = configured_catalog()
    model = catalog.require_model("configured")
    match, = catalog.detect_model("Demo")
    for device in (
        catalog.detect_device("Demo"), catalog.device_from_match(match),
        catalog.device_from_model(model.model_key), *catalog.detection_devices("Demo"),
    ):
        assert device.preparation_recipe == "session-policy"
        assert device.protocol_variant is None
    assert catalog.device_from_profile("v5x").preparation_recipe is None


def test_model_preparation_roundtrips_and_is_the_default_when_no_override_is_given():
    catalog = configured_catalog()
    device = catalog.device_from_model("configured")
    config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
    assert config["device"]["preparation_recipe"] == "session-policy"
    assert catalog.device_from_printer_config(config).preparation_recipe == "session-policy"
    del config["device"]["preparation_recipe"]
    assert catalog.device_from_printer_config(config).preparation_recipe == "session-policy"
    config["device"]["preparation_recipe"] = None
    assert catalog.device_from_printer_config(config).preparation_recipe is None


@pytest.mark.parametrize("invalid", ["", " policy", "policy ", 42])
def test_model_rejects_invalid_preparation_ids(invalid):
    catalog = configured_catalog()
    with pytest.raises(ValueError, match="preparation_recipe"):
        replace(catalog.require_model("configured"), preparation_recipe=invalid)
