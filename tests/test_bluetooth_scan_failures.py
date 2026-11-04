from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.helpers import build_capture_reporter
from timiniprint.devices import PrinterCatalog
from timiniprint.transport.bluetooth import BluetoothDiscovery
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport, ScanFailure


@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize("first_failure", [False, True])
def test_scan_keeps_failures_from_both_initial_and_retry_attempts(blocking, first_failure):
    reporter, sink = build_capture_reporter()
    discovery = BluetoothDiscovery(PrinterCatalog.load(), reporter)
    classic = DeviceInfo("Uncatalogued Radio", "AA:BB:CC:DD:EE:01", transport=DeviceTransport.CLASSIC)
    initial_failures = [ScanFailure(DeviceTransport.BLE, OSError("first scan"))] if first_failure else []
    retry_failures = [ScanFailure(DeviceTransport.BLE, TimeoutError("retry scan"))]
    replies = [([classic], initial_failures), ([], retry_failures)]
    scanner = Mock(side_effect=replies) if blocking else AsyncMock(side_effect=replies)
    scan = "scan_with_failures_blocking" if blocking else "scan_with_failures"
    with patch(f"timiniprint.transport.bluetooth.discovery.SppBackend.{scan}", scanner):
        result = discovery.scan_report_blocking() if blocking else asyncio.run(discovery.scan_report())
    assert scanner.call_count == 2
    assert scanner.call_args.kwargs == {"timeout": 5.0, "include_classic": False, "include_ble": True}
    assert result.failures == initial_failures + retry_failures
    assert len(initial_failures) == int(first_failure)
    assert result.raw_endpoints == [classic]
    assert result.devices == []
    assert discovery.devices_for_display(result) == []
    manual, = discovery.manual_targets_for_display(result)
    assert manual.transport_target.classic_endpoint.address == classic.address
    assert manual.transport_target.ble_endpoint is None
    summary, = [m.detail for m in sink.messages if m.detail.startswith("Discovery summary")]
    assert f"failures={1 + int(first_failure)}" in summary
    assert all(classic.address not in (message.detail or "") for message in sink.messages)
