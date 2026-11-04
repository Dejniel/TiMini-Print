from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from itertools import permutations
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from timiniprint.devices import BluetoothEndpointResolver, PrinterCatalog, bluetooth_connection_plan
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport, BluetoothTarget
from timiniprint.devices.profiles import ModelDetection, PrinterModel
from timiniprint.transport.bluetooth import BleakBluetoothConnector
from timiniprint.transport.bluetooth.types import DeviceTransport
from tests.test_bluetooth_target_grouping import assert_endpoints_preserved


BLE = BluetoothEndpointTransport.BLE
CLASSIC_A = BluetoothEndpoint("Shared", "AA:11:00:00:00:01")
BLE_A = BluetoothEndpoint("Shared", "AA:11:00:00:00:02", transport=BLE)
UNKNOWN_BLE = BluetoothEndpoint("Shared", "CC:33:00:00:00:03", transport=BLE)


@pytest.fixture
def catalog():
    base = PrinterCatalog.load()
    return PrinterCatalog([base.require_profile("x6h"), base.require_profile("v5x")], [
        PrinterModel(model_key="a", profile_key="x6h", origin_ids=("source.a",),
                     detections=(ModelDetection(exact_names=("Shared",), mac_prefixes=("AA:11",)),)),
        PrinterModel(model_key="b", profile_key="v5x", origin_ids=("source.b",),
                     detections=(ModelDetection(exact_names=("Shared",), mac_prefixes=("BB:22",)),)),
    ])


@pytest.mark.parametrize("unknown_transport", list(BluetoothEndpointTransport))
def test_one_catalog_compatible_pair_is_not_blocked_by_an_unmatched_peer(catalog, unknown_transport):
    resolver = BluetoothEndpointResolver(catalog)
    unknown = replace(UNKNOWN_BLE, transport=unknown_transport)
    endpoints = (CLASSIC_A, BLE_A, unknown)
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 2
        assert_endpoints_preserved(targets, endpoints)
        device, = resolver.devices_from_endpoints(order)
        assert device.model_key == "a"
        assert device.transport_target.classic_endpoint is CLASSIC_A
        assert device.transport_target.ble_endpoint is BLE_A
        assert device.transport_target.ble_candidate is None
        assert resolver.devices_for_display(order) == [device]
        manual, = resolver.manual_targets_for_display(order)
        assert manual.transport_target.ordered_endpoints(prefer_spp=False) == [unknown]


def test_two_model_pairs_with_the_same_name_remain_independent(catalog):
    resolver = BluetoothEndpointResolver(catalog)
    classic_b = replace(CLASSIC_A, address="BB:22:00:00:00:01")
    ble_b = replace(BLE_A, address="BB:22:00:00:00:02")
    endpoints = (CLASSIC_A, BLE_A, classic_b, ble_b)
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 2
        assert_endpoints_preserved(targets, endpoints)
        devices = resolver.devices_from_endpoints(order)
        assert {device.model_key for device in devices} == {"a", "b"}
        assert all(device.transport_badge == "[classic+ble]" for device in devices)
        assert all(device.transport_target.ble_candidate is None for device in devices)
        assert resolver.devices_for_display(order) == devices
        assert resolver.manual_targets_for_display(order) == []


def test_two_equally_compatible_peers_cannot_be_resolved_by_scan_order(catalog):
    resolver = BluetoothEndpointResolver(catalog)
    endpoints = (CLASSIC_A, BLE_A, replace(BLE_A, address="AA:11:00:00:00:03"))
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 3
        assert all(item.transport_target.transport_badge != "[classic+ble]" for item in targets)
        assert_endpoints_preserved(targets, endpoints)


def test_shared_model_candidates_require_unique_partners_in_both_directions(catalog):
    models = [replace(model, detections=(ModelDetection(exact_names=("Shared",),
               mac_prefixes=("AA:11", "BB:22") if model.model_key == "b" else ("AA:11",)),))
              for model in catalog.models]
    resolver = BluetoothEndpointResolver(PrinterCatalog(catalog.profiles, models))
    endpoints = (CLASSIC_A, BLE_A, replace(BLE_A, address="BB:22:00:00:00:03"))
    for order in permutations(endpoints):
        targets = resolver.transport_targets_from_endpoints(order)
        assert len(targets) == 3
        assert all(item.transport_target.transport_badge != "[classic+ble]" for item in targets)
        assert_endpoints_preserved(targets, endpoints)


def test_anonymous_candidate_does_not_become_identity_or_advertisement_evidence(catalog):
    resolver = BluetoothEndpointResolver(catalog)
    classic = replace(CLASSIC_A, address="BB:22:00:00:00:01", paired=False,
                      manufacturer_data=(b"classic",))
    ble = replace(UNKNOWN_BLE, name="", paired=True, manufacturer_data=(b"\x00\x59",))
    for order in permutations((classic, ble)):
        device, = resolver.devices_from_endpoints(order)
        target = device.transport_target
        assert target.classic_endpoint is classic and target.ble_endpoint is None
        assert target.ble_candidate is ble
        assert target.manufacturer_data == classic.manufacturer_data
        assert target.paired is False
        assert target.ordered_endpoints(prefer_spp=False) == [classic]
        assert bluetooth_connection_plan(device).endpoints == (ble, classic)
        assert resolver.devices_for_display(order) == [device]
        manual, = resolver.manual_targets_for_display(order)
        assert manual.transport_target.ble_endpoint is ble
        assert manual.transport_target.ble_candidate is None
        assert resolver.select_device([device], ble.address) is None
        assert resolver.select_transport_target(resolver.transport_targets_from_endpoints(order), ble.address) == manual
        rebuilt = catalog.device_from_printer_config(json.loads(json.dumps(catalog.serialize_printer_config(device))))
        assert rebuilt.transport_target == target
        assert bluetooth_connection_plan(rebuilt).endpoints == (ble, classic)


@pytest.mark.parametrize("case", ["two_anonymous", "two_known", "ambiguous_known", "paired_unknown", "named_ble"])
def test_anonymous_heuristic_does_not_override_other_discovery_evidence(catalog, case):
    classic = replace(CLASSIC_A, address="BB:22:00:00:00:01")
    ble = replace(UNKNOWN_BLE, name="")
    endpoints = [classic, ble]
    if case == "two_anonymous":
        endpoints.append(replace(ble, address="DD:44:00:00:00:01"))
    elif case == "two_known":
        endpoints.append(CLASSIC_A)
    elif case == "ambiguous_known":
        models = [replace(model, detections=model.detections + (ModelDetection(exact_names=("Ambiguous",)),))
                  for model in catalog.models]
        catalog = PrinterCatalog(catalog.profiles, models)
        endpoints.append(BluetoothEndpoint("Ambiguous", "DD:44:00:00:00:01"))
    elif case == "paired_unknown":
        endpoints.append(BluetoothEndpoint("Unknown", ble.address))
    elif case == "named_ble":
        endpoints[1] = replace(ble, name="Unknown BLE")
    resolver = BluetoothEndpointResolver(catalog)
    assert all(device.transport_target.ble_candidate is None for device in resolver.devices_for_display(endpoints))
    assert all(device.transport_target.ble_candidate is None for device in resolver.devices_from_endpoints(endpoints))
    assert_endpoints_preserved(resolver.transport_targets_from_endpoints(endpoints), endpoints)


@pytest.mark.parametrize("case", ["no_classic", "already_paired", "wrong_transport"])
def test_invalid_ble_candidate_targets_are_rejected(case):
    target = BluetoothTarget(CLASSIC_A, None, CLASSIC_A.address, "[classic]", ble_candidate=BLE_A)
    changes = {"classic_endpoint": None} if case == "no_classic" else (
        {"ble_endpoint": BLE_A} if case == "already_paired" else {"ble_candidate": CLASSIC_A})
    with pytest.raises(ValueError, match="BLE candidate"):
        replace(target, **changes)


@pytest.mark.parametrize("profile_key", ["v5x", "x6h"])
def test_connection_plan_applies_transport_preference_to_an_explicit_candidate(catalog, profile_key):
    target = BluetoothTarget(CLASSIC_A, None, CLASSIC_A.address, "[classic]", ble_candidate=BLE_A)
    device = catalog.device_from_profile(profile_key, transport_target=target)
    expected = (BLE_A, CLASSIC_A) if profile_key == "v5x" else (CLASSIC_A, BLE_A)
    plan = bluetooth_connection_plan(device)
    assert plan.endpoints == expected
    assert tuple(attempt.is_fallback for attempt in plan.attempts) == (False, True)


def test_connector_can_use_a_candidate_without_adopting_its_identity(catalog):
    resolver = BluetoothEndpointResolver(catalog)
    classic = replace(CLASSIC_A, address="BB:22:00:00:00:01")
    ble = replace(UNKNOWN_BLE, name="")
    device, = resolver.devices_from_endpoints((classic, ble))
    backend = MagicMock()
    backend.connect_attempts = AsyncMock()
    backend.write = AsyncMock()
    backend.transport = DeviceTransport.BLE

    async def send():
        connector = BleakBluetoothConnector()
        connection = await connector.connect(device)
        assert connection.active_ble_profile == device.ble_transport_profile
        await connection.send_standard_payload(b"test payload")

    with patch("timiniprint.transport.bluetooth.connector.SppBackend", return_value=backend):
        asyncio.run(send())
    selected, fallback = backend.connect_attempts.await_args.args[0]
    assert fallback.address == classic.address
    assert fallback.transport is DeviceTransport.CLASSIC
    assert selected.address == ble.address
    assert selected.transport is DeviceTransport.BLE
    assert selected.ble_profile == device.ble_transport_profile
    backend.write.assert_awaited_once_with(b"test payload", device.profile.stream.chunk_size,
                                           device.profile.stream.delay_ms)
    assert device.transport_target.classic_endpoint is classic
    assert device.transport_target.ble_endpoint is None
    assert device.transport_target.ble_candidate is ble
