from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from timiniprint.devices import BluetoothEndpointResolver, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from timiniprint.devices.profiles import ModelDetection, SupportedPrinterModel, UnsupportedPrinterModel
from timiniprint.protocol import ProtocolFamily
from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakBleAdapter
from timiniprint.transport.bluetooth.discovery import BluetoothDiscovery
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
from tests.helpers import build_capture_reporter


def test_manufacturer_constraints_require_actual_ad_data_not_mac_suffix():
    rule = ModelDetection(exact_names=("MX03",), manufacturer_data_lengths=(5, 7),
                          manufacturer_data_suffixes=("59",))
    assert not rule.matches("MX03", "AA:BB:CC:DD:EE:59")
    for size in (5, 7):
        assert rule.matches("MX03", "AA:BB:CC:DD:EE:00", manufacturer_data=(bytes(size - 1) + b"\x59",))
    for data in (b"\x59", bytes(6) + b"\x58", bytes(9) + b"\x59"):
        assert not rule.matches("MX03", "AA:BB:CC:DD:EE:59", manufacturer_data=(data,))
    assert not rule.matches("MX03-unknown", None, manufacturer_data=(bytes(4) + b"\x59",))


def test_mx03_switch_uses_v5x_geometry_and_never_retains_576_dot_profile():
    catalog = PrinterCatalog.load()
    plain = catalog.detect_device("MX03", "AA:BB:CC:DD:EE:59")
    assert plain.protocol_family is ProtocolFamily.V5G
    assert plain.profile.default_paper_preset.paper_width_px == 576
    selected = catalog.detect_device("MX03", "AA:BB:CC:DD:EE:00", manufacturer_data=(bytes(4) + b"\x59",))
    assert selected.protocol_family is ProtocolFamily.V5X
    assert selected.profile_key == "v5x"
    assert selected.profile.default_paper_preset.paper_width_px == 384
    assert selected.runtime_settings is None


def test_manufacturer_constraint_also_applies_to_all_of_rules():
    plain = ModelDetection(prefixes=("MX",), suffixes=("03",), all_of=True)
    constrained = ModelDetection(prefixes=("MX",), suffixes=("03",), all_of=True,
        manufacturer_data_lengths=(5,), manufacturer_data_suffixes=("59",))
    assert not constrained.matches("MX03", None)
    assert constrained.matched_specificity("MX03", None, manufacturer_data=(bytes(4) + b"\x59",)) > plain.matched_specificity("MX03", None)


def test_scan_data_survives_dedupe_resolution_and_display_candidates():
    catalog = PrinterCatalog.load()
    endpoint = DeviceInfo("MX03", "AA:BB:CC:DD:EE:00", transport=DeviceTransport.BLE,
                          manufacturer_data=(bytes(4) + b"\x59",))
    anonymous = DeviceInfo("", endpoint.address, transport=DeviceTransport.BLE)
    merged = DeviceInfo.dedupe([anonymous, endpoint])[0]
    discovery = BluetoothDiscovery(catalog)
    device = discovery.devices_from_scan([merged])[0]
    assert device.protocol_family is ProtocolFamily.V5X
    target = device.transport_target
    assert target.ble_endpoint.manufacturer_data == endpoint.manufacturer_data
    raw = BluetoothEndpoint(endpoint.name, endpoint.address, transport=BluetoothEndpointTransport.BLE,
                            manufacturer_data=endpoint.manufacturer_data)
    displayed = BluetoothEndpointResolver(catalog).devices_for_display([device], [raw])
    assert len(displayed) == 1
    assert displayed[0].model_key == device.model_key


def test_classic_and_ble_same_mac_share_evidence_without_duplicate_profiles():
    resolver = BluetoothEndpointResolver(PrinterCatalog.load())
    classic = BluetoothEndpoint("MX03", "AA:BB:CC:DD:EE:00", transport=BluetoothEndpointTransport.CLASSIC)
    ble = BluetoothEndpoint("MX03", classic.address, transport=BluetoothEndpointTransport.BLE,
                            manufacturer_data=(bytes(4) + b"\x59",))
    devices = resolver.devices_from_endpoints([classic, ble])
    displayed = resolver.devices_for_display(devices, [classic, ble])
    assert len(displayed) == 1
    assert displayed[0].protocol_family is ProtocolFamily.V5X
    assert displayed[0].transport_badge == "[classic+ble]"
    unrelated = BluetoothEndpoint("MX03", "AA:BB:CC:DD:EE:11", transport=BluetoothEndpointTransport.CLASSIC)
    other_devices = resolver.devices_from_endpoints([unrelated, ble])
    assert {device.protocol_family for device in other_devices} == {ProtocolFamily.V5G, ProtocolFamily.V5X}


@pytest.fixture
def cross_source_advertisement_catalog():
    base = PrinterCatalog.load()
    return PrinterCatalog(base.profiles, [
        SupportedPrinterModel(
            model_key="ordinary", profile_key="v5g_small_203", origin_ids=("app.a", "app.b"),
            detections=(ModelDetection(exact_names=("Shared",)),),
        ),
        SupportedPrinterModel(
            model_key="advertised", profile_key="v5x", origin_ids=("app.b",),
            detections=(ModelDetection(exact_names=("Shared",), manufacturer_data_lengths=(5,),
                                       manufacturer_data_suffixes=("59",)),),
        ),
    ])


@pytest.mark.parametrize("layout", ["classic", "ble", "both", "multiple_ble"])
def test_display_choices_keep_source_and_advertisement_context_for_every_endpoint(
    cross_source_advertisement_catalog, layout,
):
    catalog = cross_source_advertisement_catalog
    address = "AA:BB:CC:DD:EE:01"
    advertisement = (bytes(4) + b"\x59",)
    endpoints = []
    if layout != "ble":
        endpoints.append(BluetoothEndpoint("Shared", address,
            manufacturer_data=advertisement if layout == "classic" else ()))
    if layout != "classic":
        endpoints.append(BluetoothEndpoint("Shared", address, transport=BluetoothEndpointTransport.BLE,
                                          manufacturer_data=advertisement))
    if layout == "multiple_ble":
        endpoints.append(BluetoothEndpoint("Shared", "AA:BB:CC:DD:EE:02",
                                          transport=BluetoothEndpointTransport.BLE))

    resolver = BluetoothEndpointResolver(catalog)
    automatic = resolver.devices_from_endpoints(endpoints)
    assert all(device.address != address for device in automatic)
    displayed = resolver.devices_for_display(automatic, endpoints)
    expected_badges = ("[classic]", "[ble]") if layout == "multiple_ble" else (
        "[classic+ble]" if layout == "both" else f"[{layout}]",
    )
    for badge in expected_badges:
        choices = [device for device in displayed if device.address == address and device.transport_badge == badge]
        assert {device.model_key: device.origin_ids for device in choices} == {
            "ordinary": ("app.a",), "advertised": ("app.b",),
        }
        assert {device.protocol_family for device in choices} == {ProtocolFamily.V5G, ProtocolFamily.V5X}
        for device in choices:
            assert device.transport_target.manufacturer_data == advertisement
    if layout == "multiple_ble":
        other = [device for device in displayed if device.address != address]
        assert len(other) == 1
        assert other[0].model_key == "ordinary"
        assert other[0].origin_ids == ("app.a", "app.b")
        assert other[0].transport_target.manufacturer_data == ()
    else:
        assert len(displayed) == 2


@pytest.mark.parametrize("blocking", [False, True])
def test_scan_logs_resolved_profile_for_both_endpoints(blocking):
    reporter, sink = build_capture_reporter()
    discovery = BluetoothDiscovery(PrinterCatalog.load(), reporter)
    address = "AA:BB:CC:DD:EE:00"
    endpoints = [DeviceInfo("MX03", address, transport=DeviceTransport.CLASSIC),
                 DeviceInfo("MX03", address, transport=DeviceTransport.BLE,
                            manufacturer_data=(bytes(4) + b"\x59",))]
    scan = "scan_with_failures_blocking" if blocking else "scan_with_failures"
    with patch(f"timiniprint.transport.bluetooth.discovery.SppBackend.{scan}", return_value=(endpoints, [])):
        result = discovery.scan_report_blocking() if blocking else asyncio.run(discovery.scan_report())
    assert len(result.devices) == 1
    selected = result.devices[0]
    assert selected.protocol_family is ProtocolFamily.V5X
    logs = [message.detail for message in sink.messages if message.detail.startswith("Discovery matched")]
    assert len(logs) == 2
    for log in logs:
        assert f"profile={selected.profile_key}" in log
        assert f"family={selected.protocol_family.value}" in log
        assert f"model={selected.model_key}" in log


@pytest.fixture
def diagnostic_catalog():
    base = PrinterCatalog.load()
    origins = ("test",)
    return PrinterCatalog(base.profiles, [
        SupportedPrinterModel(model_key="known", profile_key="v5x", origin_ids=origins,
                              detections=(ModelDetection(exact_names=("Known", "Ambiguous")),)),
        SupportedPrinterModel(model_key="other", profile_key="v5g_small_203", origin_ids=origins,
                              detections=(ModelDetection(exact_names=("Ambiguous",)),)),
    ], [UnsupportedPrinterModel(model_key="missing", origin_ids=origins,
                                detections=(ModelDetection(exact_names=("Missing",)),))],
       {"test": "Test source"})


@pytest.mark.parametrize("name, reason", [("Unknown", "no_supported_model"),
                                          ("Missing", "known_unsupported_model"),
                                          ("Ambiguous", "ambiguous_supported_model")])
def test_scan_logs_why_unresolved_endpoints_were_ignored(diagnostic_catalog, name, reason):
    reporter, sink = build_capture_reporter()
    discovery = BluetoothDiscovery(diagnostic_catalog, reporter)
    endpoint = DeviceInfo(name, "AA:BB:CC:DD:EE:00", transport=DeviceTransport.BLE)
    with patch("timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures_blocking",
               return_value=([endpoint], [])):
        result = discovery.scan_report_blocking(include_classic=False)
    assert not result.devices
    logs = [message.detail for message in sink.messages if message.detail.startswith("Discovery ignored")]
    assert len(logs) == 1
    assert f"reason={reason}" in logs[0]
    if name == "Ambiguous":
        assert "known" in logs[0] and "other" in logs[0]
    elif name == "Missing":
        assert "candidates=missing" in logs[0]


def test_scan_logs_attached_anonymous_ble_with_the_resolved_profile(diagnostic_catalog):
    reporter, sink = build_capture_reporter()
    discovery = BluetoothDiscovery(diagnostic_catalog, reporter)
    endpoints = [DeviceInfo("Known", "AA:BB:CC:DD:EE:00", transport=DeviceTransport.CLASSIC),
                 DeviceInfo("", "BLE-UUID", transport=DeviceTransport.BLE)]
    with patch("timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures_blocking",
               return_value=(endpoints, [])):
        result = discovery.scan_report_blocking()
    assert len(result.devices) == 1
    assert result.devices[0].transport_badge == "[classic+ble]"
    logs = [message.detail for message in sink.messages if message.detail.startswith("Discovery attached")]
    assert len(logs) == 1
    assert "reason=single_ble_endpoint_for_ble_first_profile" in logs[0]
    assert "profile=v5x" in logs[0]
    assert "address=BLE-UUID" in logs[0]


def test_bleak_scanner_preserves_company_identifier_and_all_manufacturer_entries():
    ble = SimpleNamespace(name="", address="AA:BB:CC:DD:EE:00")
    adv = SimpleNamespace(local_name="MX03", manufacturer_data={0x0201: b"\x00\x00\x59", 7: b"other"})
    scanner = SimpleNamespace(discover=AsyncMock(return_value={ble.address: (ble, adv)}))
    with patch.dict("sys.modules", {"bleak": SimpleNamespace(BleakScanner=scanner)}):
        endpoints = _BleakBleAdapter().scan_blocking(0.1)
    scanner.discover.assert_awaited_once_with(timeout=0.1, return_adv=True)
    assert endpoints[0].name == "MX03"
    assert endpoints[0].manufacturer_data == (b"\x01\x02\x00\x00\x59", b"\x07\x00other")


@pytest.mark.parametrize("fields", [
    {"manufacturer_data_suffixes": ("5",)},
    {"manufacturer_data_suffixes": ("xx",)},
    {"manufacturer_data_lengths": (5,)},
    {"manufacturer_data_suffixes": ("59",), "manufacturer_data_lengths": (-1,)},
])
def test_advertisement_constraint_validation(fields):
    with pytest.raises(ValueError):
        ModelDetection(exact_names=("MX03",), **fields)


def test_regex_constraint_is_full_name_whitespace_aware_and_does_not_become_public_name():
    rule = ModelDetection(name_pattern=r"(?=.{18}$)[^#]*#[^#]*", marketing_names=("YINTIBAO-V8SPRO",))
    assert rule.matches(" P12345 # 01234567890 ", None)
    assert not rule.matches("P12345#0123456789", None)
    assert not rule.matches("P1234##01234567890", None)
    assert rule.names == ("YINTIBAO-V8SPRO",)


def test_pattern_only_rules_still_apply_exclusions_and_mac_constraints():
    rule = ModelDetection(name_pattern="P.*", marketing_names=("Printer",),
                          excluded_prefixes=("PRO",), mac_prefixes=("13:03",))
    assert rule.matches("P123", "13:03:00:00:00:01")
    assert not rule.matches("PRO123", "13:03:00:00:00:01")
    assert not rule.matches("P123", "AA:BB:00:00:00:01")
    with pytest.raises(ValueError, match="name_pattern"):
        ModelDetection(name_pattern="[", marketing_names=("Printer",))
