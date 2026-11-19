"""RX diagnostics must not alter flow gating, parsing or print-buffer logging."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.runtime.tiny import TinyRuntimeController
from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import ProtocolReplyError, execute_protocol_step
from timiniprint.protocol import ProtocolFamily, ProtocolJob, ProtocolReplyExpectation, ProtocolStep
from timiniprint.protocol.families.tiny import TINY_NOTIFY_PAUSE, TINY_NOTIFY_RESUME
from timiniprint.protocol.packet import make_packet


def captured_session(connection=None):
    reporter, sink = build_capture_reporter()
    session = RuntimeConnectionSession(connection or object(), reporter=reporter)
    return session, sink


def details(sink):
    return "\n".join(message.detail or "" for message in sink.messages)


def test_fragmented_flow_logs_complete_rx_and_full_pause_duration(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("timiniprint.printing.runtime._rx_logging.time",
                        SimpleNamespace(perf_counter=lambda: now[0]))
    connection = SimpleNamespace(set_flow_paused=Mock())
    session, sink = captured_session(connection)
    controller = TinyRuntimeController()
    controller.handle_notification(session, TINY_NOTIFY_PAUSE[:4])
    controller.handle_notification(session, TINY_NOTIFY_PAUSE[4:])
    now[0] = 12.0
    controller.handle_notification(session, TINY_NOTIFY_PAUSE)
    now[0] = 13.0
    controller.handle_notification(session, TINY_NOTIFY_RESUME)
    log = details(sink)
    assert log.count("Flow control pause:") == 2
    assert "rx=" + TINY_NOTIFY_PAUSE.hex(" ") in log
    assert "rx=" + TINY_NOTIFY_RESUME.hex(" ") in log
    assert "paused_ms=3000" in log
    assert [call.args[0] for call in connection.set_flow_paused.call_args_list] == [True, True, False]


def test_identical_statuses_are_sampled_but_changes_and_delivery_are_immediate(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("timiniprint.printing.runtime._rx_logging.time",
                        SimpleNamespace(perf_counter=lambda: now[0]))
    controller = V5CRuntimeController()
    session, sink = captured_session()
    idle = make_packet(0xA1, b"\x00", ProtocolFamily.V5C)
    printing = make_packet(0xA1, b"\x80", ProtocolFamily.V5C)
    controller.handle_notification(session, idle)
    for _ in range(100):
        controller.handle_notification(session, idle)
    assert details(sink).count("V5C status:") == 1
    now[0] = 5.0
    controller.handle_notification(session, idle)
    assert "suppressed=100" in details(sink)
    controller.handle_notification(session, printing)
    assert details(sink).count("V5C status:") == 3
    assert controller.debug_snapshot()["status_code"] == 0x80


@pytest.mark.parametrize("controller_type,buffer_type", [
    (TinyRuntimeController, bytes), (TinyRuntimeController, bytearray),
    (V5CRuntimeController, bytes), (V5CRuntimeController, bytearray),
    (V5XRuntimeController, bytes),
])
def test_unrecognized_rx_is_sampled_even_when_every_fragment_differs(controller_type, buffer_type, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("timiniprint.printing.runtime._rx_logging.time",
                        SimpleNamespace(perf_counter=lambda: now[0]))
    controller = controller_type()
    session, sink = captured_session()
    for value in range(30):
        controller.handle_notification(session, buffer_type([value]) * 5)
    assert details(sink).count("unrecognized RX") == 1
    now[0] = 5.0
    controller.handle_notification(session, b"\xfe" * 5)
    assert details(sink).count("unrecognized RX") == 2
    assert "suppressed=29" in details(sink)


@pytest.mark.parametrize("stepped", [False, True])
def test_payload_and_step_jobs_keep_tx_private_and_log_the_same_flow_rx(stepped):
    reporter, sink = build_capture_reporter()
    controller = TinyRuntimeController()
    payload = b"private-raster-buffer"
    flow_calls = []

    class Connection:
        async def send(self, job):
            await self.send_standard_payload(job.payload)

        async def send_standard_payload(self, data):
            assert data == payload
            controller.handle_notification(session, TINY_NOTIFY_PAUSE + TINY_NOTIFY_RESUME)

        def set_flow_paused(self, paused, *, payload):
            flow_calls.append(paused)

    connection = Connection()
    session = RuntimeConnectionSession(connection, reporter=reporter)
    job = ProtocolJob(payload=payload, steps=(ProtocolStep.send("bitmap", payload),) if stepped else ())
    device = PrinterCatalog.load().device_from_model("iprintit")
    asyncio.run(send_prepared_job(PreparedPrinter(device, controller), connection, job, reporter=reporter))
    assert flow_calls == [True, False]
    log = details(sink)
    assert "rx=" + TINY_NOTIFY_PAUSE.hex(" ") in log
    assert "rx=" + TINY_NOTIFY_RESUME.hex(" ") in log
    assert payload.decode() not in log and payload.hex(" ") not in log


def test_rejected_query_reports_rx_expectation_and_timing_without_extra_matching():
    calls = []
    class Connection:
        def can_query_control_packet(self):
            return True

        async def query_control_packet(self, packet, *, timeout, reply_complete):
            calls.append((packet, timeout))
            return b"NO"

    session, sink = captured_session(Connection())
    step = ProtocolStep.query("status", b"private TX", expect=ProtocolReplyExpectation.OK,
                              reply_required=True)
    with pytest.raises(ProtocolReplyError):
        asyncio.run(execute_protocol_step(session, step, timeout=.25))
    log = details(sink)
    assert "rx=4e 4f" in log and "expectation=ok" in log
    assert "elapsed_ms=" in log and "timeout_ms=250" in log
    assert calls == [(b"private TX", .25)]
    assert b"private TX".hex(" ") not in log
