from dataclasses import replace
import json

import pytest

from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog, SerialTarget
from timiniprint.devices.device import BluetoothEndpointTransport
from timiniprint.devices.model_codec import model_from_json, model_to_json


@pytest.mark.parametrize("profile_key,model_key", [("15p3", "15p3"), ("d1", "pocket_printer")])
@pytest.mark.parametrize("selection", ["profile", "model"])
@pytest.mark.parametrize("endpoints", ["classic", "ble", "both"])
@pytest.mark.parametrize("manufacturer_data", [(), (b"",), (b"\x00\xff\x80\x59", bytes(range(256)))])
def test_printer_config_roundtrip_preserves_manufacturer_bytes(
    profile_key, model_key, selection, endpoints, manufacturer_data,
):
    catalog = PrinterCatalog.load()
    classic = BluetoothEndpoint("BT-583", "AA:BB:CC:DD:EE:01", paired=True,
                                manufacturer_data=manufacturer_data) if endpoints != "ble" else None
    ble = BluetoothEndpoint("BT-583", "AA:BB:CC:DD:EE:02", transport=BluetoothEndpointTransport.BLE,
                            manufacturer_data=manufacturer_data) if endpoints != "classic" else None
    badge = "[classic+ble]" if endpoints == "both" else f"[{endpoints}]"
    target = BluetoothTarget(classic, ble, "AA:BB:CC:DD:EE:01", badge)
    device = (catalog.device_from_profile(profile_key) if selection == "profile"
              else catalog.device_from_model(model_key)).with_transport_target(target)

    config = catalog.serialize_printer_config(device)
    for key, endpoint in (("classic_endpoint", classic), ("ble_endpoint", ble)):
        entry = config["device"]["transport_target"][key]
        if endpoint is None:
            assert entry is None
        else:
            assert entry["manufacturer_data"] == [data.hex() for data in manufacturer_data]
    rebuilt = catalog.device_from_printer_config(json.loads(json.dumps(config)))
    assert rebuilt.profile == device.profile
    assert rebuilt.protocol_family is device.protocol_family
    assert rebuilt.protocol_variant == device.protocol_variant
    assert rebuilt.image_pipeline == device.image_pipeline
    assert rebuilt.transport_target == target
    for endpoint in (rebuilt.transport_target.classic_endpoint, rebuilt.transport_target.ble_endpoint):
        if endpoint is not None:
            assert isinstance(endpoint.manufacturer_data, tuple)
            assert all(isinstance(data, bytes) for data in endpoint.manufacturer_data)
    assert catalog.serialize_printer_config(rebuilt) == config


def test_endpoint_model_codec_preserves_hex_and_optional_advertisement_data():
    endpoint = BluetoothEndpoint("15P3", "AA:BB:CC:DD:EE:01", manufacturer_data=(b"\x00\xff",))
    payload = json.loads(json.dumps(model_to_json(endpoint)))
    assert payload["manufacturer_data"] == ["00ff"]
    assert model_from_json(BluetoothEndpoint, payload) == endpoint
    payload["manufacturer_data"] = ["00FF"]
    assert model_from_json(BluetoothEndpoint, payload) == endpoint
    del payload["manufacturer_data"]
    assert model_from_json(BluetoothEndpoint, payload) == replace(endpoint, manufacturer_data=())


@pytest.mark.parametrize("value", [["xyz"], ["0"], [1], [True], [None], "00ff", 42])
def test_printer_config_rejects_invalid_manufacturer_data_with_field_path(value):
    catalog = PrinterCatalog.load()
    endpoint = BluetoothEndpoint("BT-583", "AA:BB:CC:DD:EE:01")
    target = BluetoothTarget(endpoint, None, endpoint.address, "[classic]")
    device = catalog.device_from_profile("d1", transport_target=target)
    config = catalog.serialize_printer_config(device)
    config["device"]["transport_target"]["classic_endpoint"]["manufacturer_data"] = value
    with pytest.raises(ValueError, match="transport_target.classic_endpoint.manufacturer_data"):
        catalog.device_from_printer_config(config)


@pytest.mark.parametrize("target", [None, SerialTarget("/dev/test-printer", baud_rate=9600)])
def test_printer_config_roundtrip_keeps_non_bluetooth_targets(target):
    catalog = PrinterCatalog.load()
    device = catalog.device_from_profile("15p3", transport_target=target)
    config = catalog.serialize_printer_config(device)
    rebuilt = catalog.device_from_printer_config(json.loads(json.dumps(config)))
    assert rebuilt.transport_target == target
    assert catalog.serialize_printer_config(rebuilt) == config
