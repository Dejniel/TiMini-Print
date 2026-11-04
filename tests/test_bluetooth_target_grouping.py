from __future__ import annotations

from collections import Counter
from itertools import permutations

import pytest

from timiniprint.devices import BluetoothEndpointResolver, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from timiniprint.devices.profiles import ModelDetection, PrinterModel


CLASSIC = BluetoothEndpointTransport.CLASSIC
BLE = BluetoothEndpointTransport.BLE
MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_C = "AA:BB:CC:DD:EE:03"


@pytest.fixture
def resolver():
    base = PrinterCatalog.load()
    catalog = PrinterCatalog([base.require_profile("x6h"), base.require_profile("v5x")], [
        PrinterModel(model_key="known_classic", profile_key="x6h", origin_ids=("source.a",),
                     detections=(ModelDetection(exact_names=("Known Classic", "Classic Alias")),)),
        PrinterModel(model_key="known_ble", profile_key="v5x", origin_ids=("source.b",),
                     detections=(ModelDetection(exact_names=("Known BLE",)),)),
    ])
    return BluetoothEndpointResolver(catalog)


def assert_endpoints_preserved(targets, endpoints):
    flattened = [endpoint for item in targets
                 for endpoint in (item.transport_target.classic_endpoint, item.transport_target.ble_endpoint)
                 if endpoint is not None]
    assert Counter((e.transport, e.address, e.name, e.paired) for e in flattened) == Counter(
        (e.transport, e.address, e.name, e.paired) for e in endpoints
    )


@pytest.mark.parametrize("names", [("Radio", ""), ("", "Radio"), ("Radio A", "Radio B"), ("", ""), ("Radio", "Radio")])
@pytest.mark.parametrize("ble_address", [MAC_A, MAC_A.lower(), MAC_A.replace(":", "-")])
@pytest.mark.parametrize("reverse", [False, True])
def test_identical_mac_pairs_transports_without_replacing_endpoint_metadata(resolver, names, ble_address, reverse):
    classic = BluetoothEndpoint(names[0], MAC_A, paired=True)
    ble = BluetoothEndpoint(names[1], ble_address, transport=BLE, manufacturer_data=(b"\x34\x12\x59",))
    endpoints = [ble, classic] if reverse else [classic, ble]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    item, = targets
    target = item.transport_target
    assert target.transport_badge == "[classic+ble]"
    assert target.display_address == MAC_A
    assert target.ble_endpoint is ble
    assert target.classic_endpoint.manufacturer_data == ble.manufacturer_data
    assert_endpoints_preserved(targets, endpoints)
    assert resolver.select_transport_target(targets, classic.address) is item
    assert resolver.select_transport_target(targets, ble.address) is item
    for name in names:
        if name:
            assert resolver.select_transport_target(targets, name) is item


def test_mac_pairs_take_precedence_over_crossed_name_pairs_in_any_scan_order(resolver):
    endpoints = [BluetoothEndpoint("Radio A", MAC_A), BluetoothEndpoint("Radio B", MAC_B),
                 BluetoothEndpoint("Radio B", MAC_A, transport=BLE), BluetoothEndpoint("Radio A", MAC_B, transport=BLE)]
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 2
        assert {item.transport_target.display_address for item in targets} == {MAC_A, MAC_B}
        for item in targets:
            target = item.transport_target
            assert target.classic_endpoint.address == target.ble_endpoint.address
        assert_endpoints_preserved(targets, endpoints)


def test_known_aliases_use_the_same_mac_pairs_for_detection_display_and_selection(resolver):
    endpoints = [BluetoothEndpoint("Known Classic", MAC_A), BluetoothEndpoint("Classic Alias", MAC_B),
                 BluetoothEndpoint("Classic Alias", MAC_A, transport=BLE),
                 BluetoothEndpoint("Known Classic", MAC_B, transport=BLE)]
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        automatic = resolver.devices_from_endpoints(order)
        displayed = resolver.devices_for_display(order)
        assert len(targets) == len(automatic) == len(displayed) == 2
        assert automatic == displayed
        assert {device.transport_target for device in automatic} == {item.transport_target for item in targets}
        assert resolver.manual_targets_for_display(order) == []
        for device in automatic:
            target = device.transport_target
            assert target.classic_endpoint.address == target.ble_endpoint.address
            assert device.profile_key == "x6h"
            assert resolver.select_device(automatic, device.address) == device


@pytest.mark.parametrize("ble_address", [MAC_B, "12345678-1234-5678-9ABC-DEF012345678"])
def test_existing_name_pairing_still_accepts_different_addresses(resolver, ble_address):
    endpoints = [BluetoothEndpoint("Radio", MAC_A), BluetoothEndpoint("Radio", ble_address, transport=BLE)]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    item, = targets
    assert item.transport_target.transport_badge == "[classic+ble]"
    assert_endpoints_preserved(targets, endpoints)


@pytest.mark.parametrize("name", ["Radio", "Known Classic"])
def test_removing_a_mac_pair_does_not_make_an_ambiguous_name_group_unambiguous(resolver, name):
    endpoints = [BluetoothEndpoint(name, MAC_A), BluetoothEndpoint(name, MAC_B),
                 BluetoothEndpoint(name, MAC_A, transport=BLE), BluetoothEndpoint(name, MAC_C, transport=BLE)]
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 3
        pairs = [item.transport_target for item in targets if item.transport_target.transport_badge == "[classic+ble]"]
        assert len(pairs) == 1
        assert pairs[0].classic_endpoint.address == pairs[0].ble_endpoint.address == MAC_A
        assert_endpoints_preserved(targets, endpoints)


def test_duplicate_mac_entries_are_not_arbitrarily_selected_or_dropped(resolver):
    classic = BluetoothEndpoint("Radio", MAC_A)
    endpoints = [classic, classic, BluetoothEndpoint("Radio", MAC_A, transport=BLE)]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    assert len(targets) == 3
    assert all(item.transport_target.transport_badge != "[classic+ble]" for item in targets)
    assert_endpoints_preserved(targets, endpoints)


def test_duplicate_addresses_with_distinct_advertised_names_are_not_discarded(resolver):
    endpoints = [BluetoothEndpoint("Known Classic", MAC_A), BluetoothEndpoint("Classic Alias", MAC_A),
                 BluetoothEndpoint("Known Classic", MAC_B, transport=BLE)]
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 2
        assert_endpoints_preserved(targets, endpoints)


@pytest.mark.parametrize("address", ["opaque-address", "12345678-1234-5678-9ABC-DEF012345678"])
def test_opaque_ids_are_not_treated_as_real_mac_addresses(resolver, address):
    endpoints = [BluetoothEndpoint("Radio A", address), BluetoothEndpoint("Radio B", address, transport=BLE)]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    assert len(targets) == 2
    assert_endpoints_preserved(targets, endpoints)


def test_a_real_name_that_looks_like_another_endpoints_address_does_not_pair_them(resolver):
    endpoints = [BluetoothEndpoint(MAC_A, MAC_B), BluetoothEndpoint("", MAC_A, transport=BLE)]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    assert len(targets) == 2
    assert all(item.transport_target.transport_badge != "[classic+ble]" for item in targets)
    assert resolver.manual_targets_for_display(endpoints) == targets
    assert_endpoints_preserved(targets, endpoints)


@pytest.mark.parametrize("named_transport", [CLASSIC, BLE])
def test_display_keeps_one_known_model_with_its_complete_target(resolver, named_transport):
    names = ("Known Classic", "") if named_transport is CLASSIC else ("", "Known BLE")
    endpoints = [BluetoothEndpoint(names[0], MAC_A), BluetoothEndpoint(names[1], MAC_A, transport=BLE)]
    automatic = resolver.devices_from_endpoints(endpoints)
    devices = resolver.devices_for_display(endpoints)
    assert automatic == devices
    device, = devices
    assert device.model_key == ("known_classic" if named_transport is CLASSIC else "known_ble")
    assert device.transport_badge == "[classic+ble]"
    assert resolver.manual_targets_for_display(endpoints) == []


@pytest.mark.parametrize("name", ["Known Classic", "Known BLE"])
@pytest.mark.parametrize("address", [MAC_B, "12345678-1234-5678-9ABC-DEF012345678"])
def test_unrelated_anonymous_ble_stays_selectable_beside_one_known_printer(resolver, name, address):
    classic = BluetoothEndpoint(name, MAC_A)
    ble = BluetoothEndpoint("", address, transport=BLE)
    for order in permutations((classic, ble)):
        targets = resolver.transport_targets_from_endpoints(order)
        automatic = resolver.devices_from_endpoints(order)
        assert len(targets) == 2
        device, = automatic
        assert device.transport_target.classic_endpoint is classic
        assert device.transport_target.ble_endpoint is None
        assert device.transport_target.ble_candidate is (ble if name == "Known BLE" else None)
        assert resolver.devices_for_display(order) == automatic
        manual, = resolver.manual_targets_for_display(order)
        assert manual.transport_target.ble_endpoint is ble
        assert manual.transport_target.classic_endpoint is None
        assert manual.transport_target.ble_candidate is None
        assert resolver.select_transport_target(targets, address) == manual
        assert_endpoints_preserved(targets, (classic, ble))


def test_matching_mac_does_not_choose_one_model_over_the_other(resolver):
    endpoints = [BluetoothEndpoint("Known Classic", MAC_A), BluetoothEndpoint("Known BLE", MAC_A, transport=BLE)]
    assert resolver.devices_from_endpoints(endpoints) == []
    devices = resolver.devices_for_display(endpoints)
    assert {device.model_key: device.profile_key for device in devices} == {"known_classic": "x6h", "known_ble": "v5x"}
    assert len(devices) == 2
    assert all(device.transport_badge == "[classic+ble]" for device in devices)
    assert resolver.manual_targets_for_display(endpoints) == []


def test_ambiguity_on_one_transport_cannot_be_hidden_by_a_unique_name_on_its_peer(resolver):
    base = PrinterCatalog.load()
    catalog = PrinterCatalog([base.require_profile("x6h"), base.require_profile("v5x")], [
        PrinterModel(model_key="a", profile_key="x6h", origin_ids=("source.a",),
                     detections=(ModelDetection(exact_names=("Shared", "Unique")),)),
        PrinterModel(model_key="b", profile_key="v5x", origin_ids=("source.b",),
                     detections=(ModelDetection(exact_names=("Shared",)),)),
    ])
    resolver = BluetoothEndpointResolver(catalog)
    endpoints = [BluetoothEndpoint("Unique", MAC_A), BluetoothEndpoint("Shared", MAC_A, transport=BLE)]
    assert resolver.devices_from_endpoints(endpoints) == []
    choices = resolver.devices_for_display(endpoints)
    assert {device.model_key for device in choices} == {"a", "b"}
    target, = resolver.transport_targets_from_endpoints(endpoints)
    assert all(device.transport_target == target.transport_target for device in choices)
    assert resolver.manual_targets_for_display(endpoints) == []


def test_detected_aliases_produce_one_model_on_the_shared_target(resolver):
    endpoints = [BluetoothEndpoint("Known Classic", MAC_A), BluetoothEndpoint("Classic Alias", MAC_A, transport=BLE)]
    automatic = resolver.devices_from_endpoints(endpoints)
    device, = automatic
    assert resolver.devices_for_display(endpoints) == automatic
    assert device.model_key == "known_classic"
    assert device.transport_badge == "[classic+ble]"
    for endpoint in endpoints:
        assert resolver.select_device(automatic, endpoint.name) is device


def test_exact_advertised_alias_is_selected_before_another_targets_display_substring(resolver):
    endpoints = [BluetoothEndpoint("Alias extended", MAC_A),
                 BluetoothEndpoint("Long Alias description", MAC_A, transport=BLE),
                 BluetoothEndpoint("Alias", MAC_B),
                 BluetoothEndpoint("A much longer BLE name", MAC_B, transport=BLE)]
    targets = resolver.transport_targets_from_endpoints(endpoints)
    selected = resolver.select_transport_target(targets, "Alias")
    assert selected.transport_target.classic_endpoint.address == MAC_B
