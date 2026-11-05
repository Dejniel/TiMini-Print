"""Runtime identities are endpoint data, not presentation labels."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_runtime_phomemo import ReplyConnection
from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpointTransport, SerialTarget
from timiniprint.printing.runtime.prepare import prepare_connection_runtime


MAC = "AA:BB:CC:DD:EE:01"
BLE = BluetoothEndpointTransport.BLE


@pytest.fixture(scope="module")
def device():
    return PrinterCatalog.load().device_from_profile("v5x", display_name="Manual name hint")


def test_advertised_names_keep_raw_case_whitespace_and_both_endpoint_identities(device):
    target = BluetoothTarget(BluetoothEndpoint("M50 ", MAC),
                             BluetoothEndpoint("m50", MAC, transport=BLE), MAC, "[classic+ble]")
    selected = device.with_transport_target(target)
    assert selected.advertised_names == ("M50 ", "m50")
    assert replace(selected, display_name="Another UI label").advertised_names == selected.advertised_names
    assert device.advertised_names == ("Manual name hint",)


def test_advertised_names_deduplicate_exact_values_but_never_add_a_ble_candidate(device):
    classic = BluetoothEndpoint("M02", MAC)
    ble = BluetoothEndpoint("M02", MAC, transport=BLE)
    assert device.with_transport_target(BluetoothTarget(classic, ble, MAC, "[classic+ble]")).advertised_names == ("M02",)
    candidate = BluetoothEndpoint("M02H", "AA:BB:CC:DD:EE:02", transport=BLE)
    target = BluetoothTarget(classic, None, MAC, "[classic]", ble_candidate=candidate)
    assert device.with_transport_target(target).advertised_names == ("M02",)


@pytest.mark.parametrize("target", [
    None, SerialTarget("/dev/test"),
    BluetoothTarget(BluetoothEndpoint("", MAC), None, MAC, "[classic]"),
])
def test_devices_without_advertised_names_keep_the_manual_name_hint(device, target):
    assert device.with_transport_target(target).advertised_names == ("Manual name hint",)


@pytest.mark.parametrize("transport", list(BluetoothEndpointTransport))
@pytest.mark.parametrize("name", ["M02H", "T02"])
def test_phomemo_startup_uses_raw_endpoint_names_even_with_a_custom_ui_label(name, transport):
    endpoints = {item: BluetoothEndpoint(name if item is transport else "", MAC, transport=item)
                 for item in BluetoothEndpointTransport}
    target = BluetoothTarget(endpoints[BluetoothEndpointTransport.CLASSIC], endpoints[BLE], MAC, "[classic+ble]")
    device = PrinterCatalog.load().device_from_model("phomemo_m02", display_name="Desk printer", transport_target=target)
    device = replace(device, protocol_variant="m02h" if name == "M02H" else "t02")
    connection = ReplyConnection()
    connection.query_replies[b"\x1f\x11\x38"] = bytes.fromhex("1a1603")
    with patch("timiniprint.printing.runtime.phomemo.asyncio.sleep", new_callable=AsyncMock):
        context = asyncio.run(prepare_connection_runtime(device, connection, timeout=.01))
    assert connection.sent == ([b"\x1b\x40"] if name == "M02H" else [])
    assert context.capabilities.chip_type == (3 if name == "T02" else None)
