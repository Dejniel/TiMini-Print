from __future__ import annotations

import json

import pytest

from tests.test_devices_models import _profile_payload
from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, ModelMatch, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpointTransport
from timiniprint.devices.model_codec import model_from_json
from timiniprint.devices.profiles import (
    ModelDetection, PrinterProfile, PrinterModel,
)


def supported(key, source, detection, **kwargs):
    return PrinterModel(
        model_key=key, profile_key="demo", detections=(detection,),
        origin_ids=(source,) if isinstance(source, str) else source, **kwargs,
    )


def catalog_with(*models):
    return PrinterCatalog(
        [model_from_json(PrinterProfile, _profile_payload())],
        models,
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("precise", [
    ModelDetection(prefixes=("aa",)),
    ModelDetection(exact_names=("aaa",)),
    ModelDetection(prefixes=("a",), mac_suffixes=("59",)),
    ModelDetection(prefixes=("a",), manufacturer_data_suffixes=("AABB",)),
    ModelDetection(prefixes=("a",), substrings=("aa",), all_of=True),
])
def test_specificity_does_not_remove_another_sources_candidate(precise, reverse):
    models = [supported("broad", "app.a", ModelDetection(prefixes=("a",))),
              supported("precise", "app.b", precise)]
    catalog = catalog_with(*(reversed(models) if reverse else models))
    address, advertisement = "AA:BB:CC:DD:EE:59", (bytes.fromhex("00AABB"),)
    matches = catalog.detect_model("aaa", address, manufacturer_data=advertisement)
    assert {match.model.model_key for match in matches} == {"broad", "precise"}
    assert catalog.detect_device("aaa", address, manufacturer_data=advertisement) is None
    choices = catalog.detection_devices("aaa", address, manufacturer_data=advertisement)
    assert {device.model_key: device.origin_ids for device in choices} == {
        "broad": ("app.a",), "precise": ("app.b",),
    }
    for device in choices:
        assert device.profile_key == "demo"
        assert device.profile.default_paper_preset.paper_width_px == 384


def test_specificity_still_selects_a_variant_within_one_source():
    catalog = catalog_with(
        supported("broad", "app.a", ModelDetection(prefixes=("a",))),
        supported("precise", "app.a", ModelDetection(prefixes=("aa",))),
    )
    assert catalog.detect_device("aaa").model_key == "precise"


def test_equal_specificity_within_one_source_remains_ambiguous():
    catalog = catalog_with(
        supported("first", "app.a", ModelDetection(prefixes=("aa",))),
        supported("second", "app.a", ModelDetection(prefixes=("aa",))),
    )
    assert {match.model.model_key for match in catalog.detect_model("aaa")} == {"first", "second"}
    assert catalog.detect_device("aaa") is None


def test_matches_are_printable_models_with_resolved_profiles():
    model = supported("known", "app.a", ModelDetection(exact_names=("Known",)))
    catalog = catalog_with(model)
    assert catalog.detect_model("Unknown") == ()
    assert catalog.detect_device("Unknown") is None
    match, = catalog.detect_model("Known")
    assert isinstance(match, ModelMatch)
    assert match.model is model
    assert match.profile is catalog.require_profile(model.profile_key)
    assert match.origin_ids == ("app.a",)
    assert catalog.device_from_match(match) == catalog.detect_device("Known")


@pytest.mark.parametrize("same_source", [False, True])
def test_case_fallback_is_scoped_to_the_source(same_source):
    catalog = catalog_with(
        supported("literal", "app.a", ModelDetection(prefixes=("a",))),
        supported("folded", "app.a" if same_source else "app.b", ModelDetection(prefixes=("AA",))),
    )
    keys = {match.model.model_key for match in catalog.detect_model("aaa")}
    assert keys == ({"literal"} if same_source else {"literal", "folded"})


def test_raw_case_pattern_is_not_overridden_by_source_case_fallback():
    catalog = catalog_with(
        supported("literal", "app.a", ModelDetection(prefixes=("a",))),
        supported("strict", "app.b", ModelDetection(prefixes=("AA",), name_pattern="(?-i:AA.*)")),
    )
    assert catalog.detect_device("aaa").model_key == "literal"


def test_multi_source_model_can_win_one_source_and_is_returned_only_once():
    catalog = catalog_with(
        supported("shared", ("app.a", "app.b", "sdk.c"), ModelDetection(prefixes=("a",))),
        supported("precise", "app.a", ModelDetection(prefixes=("aa",))),
    )
    matches = catalog.detect_model("aaa")
    assert sorted(match.model.model_key for match in matches) == ["precise", "shared"]
    assert {match.model.model_key: match.origin_ids for match in matches} == {
        "precise": ("app.a",), "shared": ("app.b", "sdk.c"),
    }
    shared = next(match for match in matches if match.model.model_key == "shared")
    assert shared.model.origin_ids == ("app.a", "app.b", "sdk.c")
    device = catalog.device_from_match(shared)
    assert device.origin_ids == ("app.b", "sdk.c")
    config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
    assert catalog.device_from_printer_config(config) == device
    del config["device"]["origin_ids"]
    assert catalog.device_from_printer_config(config).origin_ids == shared.model.origin_ids
    assert catalog.detect_model("unrelated") == ()


@pytest.mark.parametrize("origins", ["app.a", [""], [" app.a"], [None], ["other.app"]])
def test_config_cannot_attribute_a_model_to_an_unrelated_or_invalid_source(origins):
    catalog = catalog_with(supported("known", "app.a", ModelDetection(prefixes=("a",))))
    config = catalog.serialize_printer_config(catalog.device_from_model("known"))
    config["device"]["origin_ids"] = origins
    with pytest.raises(RuntimeError, match="origin_ids"):
        catalog.device_from_printer_config(config)


@pytest.mark.parametrize("shared_sources", [("app.b",), ("app.a", "app.b")])
@pytest.mark.parametrize("layout", ["classic", "ble", "both_ble", "both_classic", "both", "conflicting"])
def test_target_advertisement_evidence_survives_detection_and_config_roundtrip(shared_sources, layout):
    catalog = catalog_with(
        supported("ordinary", shared_sources, ModelDetection(prefixes=("a",))),
        supported("advertised", "app.b", ModelDetection(
            prefixes=("a",), manufacturer_data_suffixes=("59",), manufacturer_data_lengths=(5,),
        )),
    )
    advertisement = (bytes.fromhex("0000000059"),)
    classic = BluetoothEndpoint(
        "aaa", "AA:BB:CC:DD:EE:01",
        manufacturer_data=(bytes.fromhex("0000000058"),) if layout == "conflicting" else (
            advertisement if layout in ("classic", "both_classic", "both") else ()
        ),
    ) if layout != "ble" else None
    ble = BluetoothEndpoint(
        "aaa", "AA:BB:CC:DD:EE:01", transport=BluetoothEndpointTransport.BLE,
        manufacturer_data=advertisement if layout != "both_classic" else (),
    ) if layout != "classic" else None
    badge = "[classic+ble]" if classic and ble else "[classic]" if classic else "[ble]"
    target = BluetoothTarget(classic, ble, "AA:BB:CC:DD:EE:01", badge)
    choices = catalog.detection_devices("aaa", target.display_address, transport_target=target)
    expected = {"advertised": ("app.b",)}
    if "app.a" in shared_sources:
        expected["ordinary"] = ("app.a",)
    assert {device.model_key: device.origin_ids for device in choices} == expected
    automatic = catalog.detect_device("aaa", target.display_address, transport_target=target)
    if len(expected) == 1:
        assert automatic == choices[0]
    else:
        assert automatic is None
    for device in choices:
        config = json.loads(json.dumps(catalog.serialize_printer_config(device)))
        assert catalog.device_from_printer_config(config) == device

    explicit = (bytes.fromhex("0000000058"),)
    override = catalog.detect_device(
        "aaa", target.display_address, transport_target=target, manufacturer_data=explicit,
    )
    assert override.model_key == "ordinary"
    assert override.origin_ids == shared_sources
