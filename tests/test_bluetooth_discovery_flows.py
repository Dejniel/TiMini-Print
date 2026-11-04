"""Scan results, catalog choices and raw targets must describe the same peers."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from timiniprint.devices import PrinterCatalog, bluetooth_connection_plan
from timiniprint.devices.profiles import ModelDetection, PrinterModel
from timiniprint.transport.bluetooth import BluetoothDiscovery
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


@pytest.fixture
def catalog():
    profile = PrinterCatalog.load().require_profile("x6h")
    return PrinterCatalog([profile], [PrinterModel(
        model_key="known", profile_key=profile.profile_key, origin_ids=("test",),
        detections=(ModelDetection(exact_names=("Known", "Alias")),),
    )])


@pytest.mark.parametrize("blocking", [False, True])
def test_scan_display_selection_and_connection_plan_use_the_same_mac_pairs(catalog, blocking):
    endpoints = [
        DeviceInfo("Known", MAC_A, transport=DeviceTransport.CLASSIC),
        DeviceInfo("Alias", MAC_B, transport=DeviceTransport.CLASSIC),
        DeviceInfo("Alias", MAC_A, transport=DeviceTransport.BLE),
        DeviceInfo("Known", MAC_B, transport=DeviceTransport.BLE),
    ]
    discovery = BluetoothDiscovery(catalog)
    scan = "scan_with_failures_blocking" if blocking else "scan_with_failures"
    with patch(f"timiniprint.transport.bluetooth.discovery.SppBackend.{scan}", return_value=(endpoints, [])) as scanner:
        result = discovery.scan_report_blocking() if blocking else asyncio.run(discovery.scan_report())
    assert scanner.call_count == 1
    assert result.raw_endpoints == endpoints
    assert len(result.devices) == 2
    assert discovery.devices_for_display(result) == result.devices
    assert discovery.manual_targets_for_display(result) == []
    for device in result.devices:
        plan = bluetooth_connection_plan(device)
        assert tuple(endpoint.transport.value for endpoint in plan.endpoints) == ("classic", "ble")
        assert {endpoint.address for endpoint in plan.endpoints} == {device.address}

    with patch("timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures", return_value=(endpoints, [])):
        selected = asyncio.run(discovery.resolve_transport_target(MAC_B))
    device = next(device for device in result.devices if device.address == MAC_B)
    assert selected.transport_target == device.transport_target


def test_both_endpoint_aliases_select_the_same_automatically_detected_device(catalog):
    endpoints = [DeviceInfo("Known", MAC_A, transport=DeviceTransport.CLASSIC),
                 DeviceInfo("Alias", MAC_A, transport=DeviceTransport.BLE)]
    discovery = BluetoothDiscovery(catalog)
    with patch("timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures", return_value=(endpoints, [])):
        classic = asyncio.run(discovery.resolve_device("Known"))
        ble = asyncio.run(discovery.resolve_device("Alias"))
    assert classic == ble
    assert classic.transport_badge == "[classic+ble]"
