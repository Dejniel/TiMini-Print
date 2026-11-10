"""Selected RX diagnostics are useful without turning DEBUG into a wire dump."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.step_execution import execute_protocol_steps
from timiniprint.protocol import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep


def test_rx_preview_distinguishes_missing_empty_and_binary_replies():
    assert reporting.rx_preview(None) == "<none>"
    assert reporting.rx_preview(b"") == "<empty>"
    assert reporting.rx_preview(bytes.fromhex("000000b901")) == "00 00 00 b9 01"
    assert reporting.rx_preview(b"HV:9;SV:2.0") == b"HV:9;SV:2.0".hex(" ")


def test_rx_preview_bounds_large_replies_without_including_the_tail():
    data = bytes(range(64)) + b"hidden reply tail" * 100
    preview = reporting.rx_preview(data)
    assert preview == data[:64].hex(" ") + f" ... ({len(data)} bytes)"
    assert b"hidden reply tail".hex(" ") not in preview


@pytest.mark.parametrize("address", [
    "DC:0D:51:C0:6B:F7", "dc-0d-51-c0-6b-f7", "DC0D51C06BF7",
    "12345678-1234-5678-9ABC-DC0D51C06BF7",
])
@pytest.mark.parametrize("padding", [0, 60])
def test_rx_preview_masks_textual_addresses_even_across_the_preview_boundary(address, padding):
    data = bytes(padding) + address.encode() + b" revision"
    preview = reporting.rx_preview(data)
    assert "<address>" in preview
    assert address.encode().hex(" ") not in preview
    assert data[:64].hex(" ") not in preview


@pytest.mark.parametrize("data", [
    b"reply /private/customer-label.png", b"content://documents/42",
    b"\x00FILE:/private/customer-document", b"URN:private:document",
])
def test_rx_preview_omits_obvious_document_references(data):
    assert reporting.rx_preview(data) == "<document reference omitted>"


@pytest.mark.parametrize("notification_only", [False, True])
def test_protocol_step_results_log_rx_but_keep_send_and_query_tx_in_trace(notification_only):
    packet = b"private-control-request"
    image = b"private-raster-from-customer-label.png"
    reply = b"OK"
    completion = bytes(range(128))
    connection = SimpleNamespace(
        send_standard_payload=AsyncMock(),
        can_query_control_packet=lambda: not notification_only,
        query_control_packet=AsyncMock(return_value=reply),
        can_send_control_packet_wait_notification=lambda: notification_only,
        send_control_packet_wait_notification=AsyncMock(return_value=reply),
        can_wait_for_reply=lambda: True,
        wait_for_reply=AsyncMock(return_value=completion),
    )
    reporter, sink = build_capture_reporter(min_level=reporting.ReportLevel.TRACE)
    session = RuntimeConnectionSession(connection, reporter=reporter)
    steps = (
        ProtocolStep.send("bitmap", image),
        ProtocolStep.query("status", packet, expect=ProtocolReplyExpectation.OK, reply_required=True),
        ProtocolStep.wait("completion", reply_matcher=ProtocolReplyMatcher(complete=bool)),
    )
    assert asyncio.run(execute_protocol_steps(session, steps, timeout=.1))
    debug = "\n".join(message.detail or "" for message in sink.messages
                      if message.level >= reporting.ReportLevel.DEBUG)
    trace = "\n".join(message.detail or "" for message in sink.messages
                      if message.level == reporting.ReportLevel.TRACE)
    assert "query status" in debug and "rx=4f 4b" in debug
    assert "wait completion" in debug and reporting.rx_preview(completion) in debug
    for tx in (packet, image):
        assert tx.decode() not in debug and tx.hex(" ") not in debug
        assert tx[:16].hex(" ") in trace and tx[-16:].hex(" ") in trace
    assert completion.hex(" ") not in debug
    connection.send_standard_payload.assert_awaited_once_with(image)


def test_transport_does_not_dump_every_unsolicited_notification_in_debug():
    from timiniprint.devices.bluetooth_profiles import BleTransportProfile
    from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
    from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession

    reporter, sink = build_capture_reporter(min_level=reporting.ReportLevel.TRACE)
    session = _BleakTransportSession(BleTransportProfile(), _BleWriteEndpointResolver(), reporter)
    payload = bytes.fromhex("000000b901")
    for _ in range(100):
        session.handle_notification(payload)
    assert not any(payload.hex() in (message.detail or "") or payload.hex(" ") in (message.detail or "")
                   for message in sink.messages if message.level >= reporting.ReportLevel.DEBUG)
    assert any(payload.hex() in (message.detail or "") for message in sink.messages
               if message.level == reporting.ReportLevel.TRACE)
