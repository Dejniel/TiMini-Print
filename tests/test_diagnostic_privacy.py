from __future__ import annotations

import errno
import json
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from timiniprint import reporting
from timiniprint.transport.bluetooth.device_information import BleDeviceInformation
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.types import DeviceInfo


class Capture(reporting.ReportSink):
    def __init__(self):
        self.messages = []

    def emit(self, message):
        self.messages.append(message)


@pytest.mark.parametrize("address", [
    "DC:0D:51:C0:6B:F7", "dc-0d-51-c0-6b-f7", "DC0D51C06BF7",
    "12345678-1234-5678-9ABC-DC0D51C06BF7",
])
def test_device_metadata_masks_addresses_without_losing_model_or_revision(address):
    value = reporting.device_metadata_text(f"MPT-II {address} firmware 1.2")
    assert address not in value
    assert value == "MPT-II <address> firmware 1.2"


@pytest.mark.parametrize("value", [
    "customer-label.png", "customer-document.pdf", "/tmp/customer-label.png", r"C:\private\customer-label.png", "file:///private/document.pdf",
    "content://documents/private", "urn:private:document", "device\nprivate", "firmware\x00private", "a" * 200, b"raster",
])
def test_device_metadata_does_not_publish_paths_uris_or_unbounded_binary_text(value):
    assert reporting.device_metadata_text(value) is None


def test_exception_summary_preserves_os_reason_without_exception_content():
    private = "DC:0D:51:C0:6B:F7 /private/customer-label.png raster=0123456789abcdef"
    cause = PermissionError(errno.EACCES, private, "/private/customer-label.png")
    error = RuntimeError(private)
    error.__cause__ = cause
    summary = reporting.exception_summary(error)
    assert summary == "RuntimeError <- PermissionError(errno=13)"
    for value in (private, "customer-label", "0123456789abcdef", "DC:0D:51:C0:6B:F7"):
        assert value not in summary


def test_exception_summary_is_bounded_even_with_a_cycle():
    error = RuntimeError("private")
    error.__cause__ = error
    assert reporting.exception_summary(error) == "RuntimeError"


def test_device_information_debug_keeps_metadata_but_not_addresses_or_native_error_text():
    address = "DC:0D:51:C0:6B:F7"
    info = BleDeviceInformation(
        address, "now", True,
        {"manufacturer": "ISSC", "model": f"MPT-II {address}", "firmware": "1.2",
         "hardware": "/private/customer-label.png", "unknown": "private-raster"},
        {"software": f"failed {address} /private/customer-label.png", "unknown": "private-raster"},
    )
    sink = Capture()
    reporter = reporting.Reporter([sink], min_level=reporting.ReportLevel.TRACE)
    info.log(reporter)
    debug, = [message for message in sink.messages if message.level is reporting.ReportLevel.DEBUG]
    fields = json.loads(debug.detail)
    assert fields["service_present"] is True
    assert fields["values"] == {"manufacturer": "ISSC", "model": "MPT-II <address>", "firmware": "1.2"}
    assert fields["errors"] == {"software": "read_failed"}
    assert fields["available_fields"] == ["firmware", "hardware", "manufacturer", "model"]
    assert debug.exc is None
    for value in (address, "private", "customer-label", "unknown"):
        assert value not in repr(debug)
    trace, = [message for message in sink.messages if message.level is reporting.ReportLevel.TRACE]
    assert address in trace.detail
    assert "customer-label" in trace.detail


def test_device_information_read_failure_categories_remain_visible():
    info = BleDeviceInformation("private", "now", True, {},
                                {"model": "timeout", "firmware": "not_readable", "software": "invalid_utf8"})
    sink = Capture()
    info.log(reporting.Reporter([sink]))
    message, = sink.messages
    assert json.loads(message.detail)["errors"] == info.errors


@pytest.mark.parametrize("fails", [False, True])
def test_native_connection_debug_keeps_transport_and_channel_without_addresses_or_error_payload(fails):
    address = "DC:0D:51:C0:6B:F7"
    sock = MagicMock()
    if fails:
        sock.connect.side_effect = PermissionError(13, f"{address} /private/customer-label.png secret raster")
    adapter = SimpleNamespace(ensure_paired=lambda *args: None,
                              resolve_rfcomm_channels=lambda address: [1], create_socket=lambda *args, **kwargs: sock)
    sink = Capture()
    reporter = reporting.Reporter([sink], min_level=reporting.ReportLevel.TRACE)
    backend = SppBackend(reporter)

    async def connect():
        try:
            await backend.connect(DeviceInfo("customer-label.png", address))
        finally:
            await backend.disconnect()

    with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=adapter):
        if fails:
            with pytest.raises(RuntimeError):
                asyncio.run(connect())
        else:
            asyncio.run(connect())
    debug = "\n".join(repr(message) for message in sink.messages if message.level >= reporting.ReportLevel.DEBUG)
    assert "transport=classic" in debug
    assert "RFCOMM channel 1" in debug
    for private in (address, "customer-label.png", "secret raster"):
        assert private not in debug
    if fails:
        assert "PermissionError(errno=13)" in debug
    assert any(address in (message.detail or "") for message in sink.messages if message.level is reporting.ReportLevel.TRACE)
