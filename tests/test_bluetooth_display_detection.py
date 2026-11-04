from __future__ import annotations

import pytest

from timiniprint.devices import BluetoothEndpointResolver, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from timiniprint.devices.profiles import ModelDetection, PrinterModel


@pytest.fixture
def catalog():
    profile = PrinterCatalog.load().require_profile("x6h")
    return PrinterCatalog([profile], [
        PrinterModel(
            model_key="ordinary", profile_key=profile.profile_key,
            origin_ids=("app.a", "app.b"),
            detections=(ModelDetection(prefixes=("E2",)),),
        ),
        PrinterModel(
            model_key="mac_variant", profile_key=profile.profile_key,
            origin_ids=("app.a",),
            detections=(ModelDetection(prefixes=("E2",), mac_suffixes=("59",)),),
        ),
        PrinterModel(
            model_key="advertised_variant", profile_key=profile.profile_key,
            origin_ids=("app.b",),
            detections=(ModelDetection(prefixes=("E2",), manufacturer_data_suffixes=("59",)),),
        ),
    ])


@pytest.mark.parametrize("layout", ["classic", "ble", "both"])
@pytest.mark.parametrize("address", ["E2:11:22:33:44:55", "E2112233-4455-6677-8899-AABBCCDDEEFF"])
def test_unnamed_endpoint_is_manual_even_when_its_display_address_matches_a_name(catalog, layout, address):
    endpoints = [
        BluetoothEndpoint("", address, transport=transport)
        for transport in BluetoothEndpointTransport
        if layout == "both" or transport.value == layout
    ]
    resolver = BluetoothEndpointResolver(catalog)
    assert resolver.devices_from_endpoints(endpoints) == []
    assert resolver.devices_for_display([], endpoints) == []
    manual, = resolver.manual_targets_for_display([], endpoints)
    assert manual.display_name == address
    assert manual.transport_target.display_address == address
    assert manual.transport_target.transport_badge == (
        "[classic+ble]" if layout == "both" else f"[{layout}]"
    )
    for endpoint in (manual.transport_target.classic_endpoint, manual.transport_target.ble_endpoint):
        if endpoint is not None:
            assert endpoint.name == ""


@pytest.mark.parametrize("layout", ["classic", "ble", "both"])
@pytest.mark.parametrize("name", ["E200", "E2:11:22:33:44:55"])
def test_actual_advertised_name_still_matches_and_merged_choices_are_not_duplicated(catalog, layout, name):
    endpoints = [
        BluetoothEndpoint(name, "AA:BB:CC:DD:EE:01", transport=transport)
        for transport in BluetoothEndpointTransport
        if layout == "both" or transport.value == layout
    ]
    resolver = BluetoothEndpointResolver(catalog)
    automatic = resolver.devices_from_endpoints(endpoints)
    device, = resolver.devices_for_display(automatic, endpoints)
    assert device.model_key == "ordinary"
    assert device.origin_ids == ("app.a", "app.b")
    assert device.display_name == name
    assert device.transport_badge == ("[classic+ble]" if layout == "both" else f"[{layout}]")
    assert resolver.manual_targets_for_display(automatic, endpoints) == []


@pytest.mark.parametrize("same_mac", [False, True])
@pytest.mark.parametrize("advertised_transport", list(BluetoothEndpointTransport))
def test_grouped_candidates_use_each_endpoints_own_name_mac_and_advertisement(
    catalog, same_mac, advertised_transport,
):
    ble_address = "AA:BB:CC:DD:EE:59"
    advertisement = (b"\x00\x59",)
    endpoints = [
        BluetoothEndpoint("E200", ble_address if same_mac else "AA:BB:CC:DD:EE:01",
                          manufacturer_data=advertisement if advertised_transport is BluetoothEndpointTransport.CLASSIC else ()),
        BluetoothEndpoint("e200", ble_address, transport=BluetoothEndpointTransport.BLE,
                          manufacturer_data=advertisement if advertised_transport is BluetoothEndpointTransport.BLE else ()),
    ]
    resolver = BluetoothEndpointResolver(catalog)
    devices = resolver.devices_for_display([], endpoints)
    expected = {"mac_variant": ("app.a",), "advertised_variant": ("app.b",)}
    if not same_mac:
        expected["ordinary"] = ("app.a", "app.b")
    elif advertised_transport is BluetoothEndpointTransport.CLASSIC:
        expected["ordinary"] = ("app.b",)
    assert {device.model_key: device.origin_ids for device in devices} == expected
    assert len(devices) == len(expected)
    for device in devices:
        assert device.transport_badge == "[classic+ble]"
        assert device.transport_target.classic_endpoint.name == "E200"
        assert device.transport_target.ble_endpoint.name == "e200"
    assert resolver.manual_targets_for_display([], endpoints) == []
