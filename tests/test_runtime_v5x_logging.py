from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.protocol import ProtocolFamily
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.steps import ProtocolStep


def public_log(sink):
    return "\n".join(repr(message) for message in sink.messages
                     if message.level >= reporting.ReportLevel.DEBUG)


@pytest.mark.parametrize("payload,expected", [
    (b"\xff" * 9, "no readable firmware payload"),
    (b"FW1.0.22", "version=FW1.0.22"),
    (b"FW AA:BB:CC:DD:EE:01", "version=FW <address>"),
    (b"/private/customer-label.png", "version=<unavailable>"),
])
def test_firmware_diagnostics_keep_metadata_private_and_preview_unreadable_rx(payload, expected):
    reporter, sink = build_capture_reporter()
    controller = V5XRuntimeController()
    session = RuntimeConnectionSession(object(), reporter=reporter)
    packet = make_packet(0xB1, payload, ProtocolFamily.V5X)
    controller.handle_notification(session, packet)
    debug = public_log(sink)
    assert expected in debug
    if payload == b"\xff" * 9:
        assert "rx=" + payload.hex(" ") in debug
    for private in (packet.hex(), payload.hex(), "AA:BB:CC:DD:EE:01", "customer-label.png", "/private/"):
        assert private not in debug
    assert controller.debug_snapshot()["firmware_version"] == payload.decode("ascii", errors="ignore")


@pytest.mark.parametrize("payload,expected", [(b"\x62", "density=98"), (b"private-raster", "density=<unavailable>")])
def test_unchanged_density_logs_only_a_decoded_numeric_value(payload, expected):
    reporter, sink = build_capture_reporter()
    controller = V5XRuntimeController()
    controller.debug_update(last_density_payload=payload)
    connection = SimpleNamespace(send_control_packet=AsyncMock(return_value=True))
    session = RuntimeConnectionSession(connection, reporter=reporter)
    steps = (ProtocolStep.send("density", make_packet(0xA2, payload, ProtocolFamily.V5X)),)
    assert asyncio.run(controller.send_protocol_steps(session, steps, timeout=.1))
    assert expected in public_log(sink)
    assert payload.hex() not in public_log(sink)
    assert "private-raster" not in public_log(sink)
    connection.send_control_packet.assert_not_awaited()


def test_signing_failure_keeps_native_exception_details_in_trace_only():
    private = "AA:BB:CC:DD:EE:01 /private/customer-label.png"
    reporter, sink = build_capture_reporter(min_level=reporting.ReportLevel.TRACE)
    connection = SimpleNamespace(send_control_packet=AsyncMock(side_effect=PermissionError(13, private)))
    session = RuntimeConnectionSession(connection, reporter=reporter)
    controller = V5XRuntimeController()
    async def run():
        controller.handle_notification(session, make_packet(0xB3, bytes(range(10)), ProtocolFamily.V5X))
        await asyncio.sleep(0)
        await controller.stop(session)
    asyncio.run(run())
    debug = public_log(sink)
    assert "PermissionError(errno=13)" in debug
    for value in ("AA:BB:CC:DD:EE:01", "/private/", "customer-label.png"):
        assert value not in debug
    assert any(private in (message.detail or "") for message in sink.messages
               if message.level == reporting.ReportLevel.TRACE)
