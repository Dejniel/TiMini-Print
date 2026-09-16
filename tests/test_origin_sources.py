from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.bluetooth_resolver import BluetoothEndpointResolver
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.devices.profiles import (
    ModelDetection, SupportedPrinterModel, UnsupportedPrinterModel,
)


@pytest.mark.parametrize("model_type", [SupportedPrinterModel, UnsupportedPrinterModel])
def test_catalog_models_require_sources(model_type):
    payload = {"model_key": "demo", "detections": [], "marketing_names": ["Demo"]}
    with pytest.raises(ValueError, match="origin_ids.*required"):
        model_from_json(model_type, payload)
    for invalid in ([], [""], [" "], [" vendor"], ["vendor "]):
        with pytest.raises(ValueError, match="origin IDs"):
            model_from_json(model_type, dict(payload, origin_ids=invalid))


@pytest.mark.parametrize("model_type", [SupportedPrinterModel, UnsupportedPrinterModel])
def test_source_ids_are_opaque_and_roundtrip(model_type):
    payload = {"model_key": "demo", "detections": [], "marketing_names": ["Demo"],
               "origin_ids": ["com.example.app", "vendor-sdk", "vendor.manual"]}
    if model_type is SupportedPrinterModel:
        payload["profile_key"] = "demo"
    model = model_from_json(model_type, payload)
    assert model.origin_ids == tuple(payload["origin_ids"])
    assert model_from_json(model_type, model_to_json(model)) == model


@pytest.fixture
def source_catalog():
    base = PrinterCatalog.load()
    app_model = replace(
        base.require_model("pocket_printer"), model_key="app_model",
        marketing_names=(), detections=(ModelDetection(exact_names=("Shared",)),),
        origin_ids=("com.example.app",),
    )
    manual_model = replace(app_model, model_key="manual_model", origin_ids=("vendor.manual",))
    return PrinterCatalog(
        [base.device_from_model("pocket_printer").profile], [app_model, manual_model],
        origin_names={"com.example.app": "Printing app", "vendor.manual": "Manufacturer ESC/POS"},
    )


def test_source_names_explain_ambiguity_without_selecting_a_protocol(source_catalog):
    assert {match.model.model_key for match in source_catalog.detect_model("Shared")} == {
        "app_model", "manual_model",
    }
    assert source_catalog.detect_device("Shared") is None
    with pytest.raises(RuntimeError) as error:
        source_catalog.device_from_key("Shared")
    assert "app_model (Printing app)" in str(error.value)
    assert "manual_model (Manufacturer ESC/POS)" in str(error.value)
    device = source_catalog.device_from_model("manual_model")
    restored = source_catalog.device_from_printer_config(
        source_catalog.serialize_printer_config(device),
    )
    assert restored.origin_ids == ("vendor.manual",)
    assert source_catalog.origin_names(restored.origin_ids) == ("Manufacturer ESC/POS",)


def test_scan_choices_keep_both_source_labels(source_catalog):
    resolver = BluetoothEndpointResolver(source_catalog)
    endpoints = [BluetoothEndpoint(
        name="Shared", address="11:22:33:44:55:66", transport=BluetoothEndpointTransport.BLE,
    )]
    resolved = resolver.devices_from_endpoints(endpoints)
    assert resolved == []
    choices = resolver.devices_for_display(resolved, endpoints)
    assert {device.model_key: source_catalog.origin_names(device.origin_ids) for device in choices} == {
        "app_model": ("Printing app",), "manual_model": ("Manufacturer ESC/POS",),
    }


def test_registered_sources_must_cover_supported_and_unsupported_models(source_catalog):
    profile = source_catalog.device_from_model("manual_model").profile
    model = source_catalog.require_model("manual_model")
    with pytest.raises(ValueError, match="missing IDs: vendor.manual"):
        PrinterCatalog([profile], [model], origin_names={"another-source": "Other"})
    unsupported = UnsupportedPrinterModel(
        model_key="unknown", detections=(), marketing_names=("Unknown",), origin_ids=("unknown-source",),
    )
    with pytest.raises(ValueError, match="missing IDs: unknown-source"):
        PrinterCatalog([profile], [model], [unsupported], origin_names={"vendor.manual": "Manual"})
    # Custom catalogs may omit the label registry and show their source IDs directly.
    catalog = PrinterCatalog([profile], [model])
    assert catalog.origin_names(model.origin_ids) == ("vendor.manual",)
