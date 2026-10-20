import asyncio
import io

import pytest

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import execute_protocol_step
from timiniprint.protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from timiniprint.transport.bluetooth.adapters.bleak_adapter_diagnostics import report_ble_write_plan


class Connection:
    def __init__(self):
        self.sent = []

    async def send(self, job):
        self.sent.append(job.payload)

    async def send_standard_payload(self, data):
        self.sent.append(data)

    async def query_control_packet(self, data, **kwargs):
        self.sent.append(data)
        return b"reply-private"

    def can_query_control_packet(self):
        return True


def logs():
    normal, full = io.StringIO(), io.StringIO()
    reporter = reporting.Reporter([
        reporting.StderrSink(stream=normal, levels={"status", "debug", "warning", "error"}),
        reporting.StderrSink(stream=full, levels={"status", "debug", "trace", "warning", "error"}),
    ])
    return reporter, normal, full


@pytest.mark.parametrize("operation", ["send", "query"])
@pytest.mark.parametrize("payload", [b"private", bytes(range(64))])
def test_step_logs_keep_counts_but_only_trace_contains_bytes(operation, payload):
    reporter, normal, full = logs()
    connection = Connection()
    session = RuntimeConnectionSession(connection, reporter=reporter)
    step = (ProtocolStep.send("raster", payload) if operation == "send"
            else ProtocolStep.query("raster", payload, expect=ProtocolReplyExpectation.NONE))

    asyncio.run(execute_protocol_step(session, step, timeout=0.1))

    assert connection.sent == [payload]
    assert "bytes=" in normal.getvalue()
    assert "data=" not in normal.getvalue()
    assert "head=" not in normal.getvalue()
    assert "tx=" not in normal.getvalue()
    assert "rx=" not in normal.getvalue()
    assert payload[:16].hex(" ") in full.getvalue()
    if operation == "query":
        assert b"reply-private".hex(" ") not in normal.getvalue()
        assert b"reply-private".hex(" ") in full.getvalue()


def test_required_reply_error_does_not_promote_raw_bytes_to_error_level():
    reporter, normal, full = logs()
    session = RuntimeConnectionSession(Connection(), reporter=reporter)
    step = ProtocolStep.query(
        "print start", b"Q", expect=ProtocolReplyExpectation.NONE, reply_required=True,
        reply_matcher=ProtocolReplyMatcher(complete=lambda data: True, matches=lambda data: False),
    )
    with pytest.raises(RuntimeError) as error:
        asyncio.run(execute_protocol_step(session, step, timeout=0.1))
    reporter.error(exc=error.value)
    assert "received_bytes=13" in normal.getvalue()
    assert b"reply-private".hex(" ") not in normal.getvalue()
    assert b"reply-private".hex(" ") in full.getvalue()


@pytest.mark.parametrize("step_based", [False, True])
def test_payload_and_step_jobs_are_sent_unchanged_without_content_in_debug(step_based):
    reporter, normal, full = logs()
    payload = b"private-print-content"
    connection = Connection()
    job = (ProtocolJob(steps=(ProtocolStep.send("raster", payload),)) if step_based
           else ProtocolJob(payload=payload))
    prepared = PreparedPrinter(PrinterCatalog.load().device_from_profile("v5x"))
    asyncio.run(send_prepared_job(prepared, connection, job, reporter=reporter))
    assert connection.sent == [payload]
    assert payload.hex(" ") not in normal.getvalue()
    if step_based:
        assert payload.hex(" ") in full.getvalue()


def test_ble_write_plan_keeps_timing_and_chunking_separate_from_payload():
    reporter, normal, full = logs()
    payload = b"private-raster"
    report_ble_write_plan(
        reporter, response=False, strategy="test", char_uuid="ff02", payload_bytes=len(payload),
        mtu_payload=20, chunk_size=20, chunk_count=1, reserve=0, delay_ms=6,
        payload_head=payload, payload_tail=payload,
    )
    assert "delay_ms=6" in normal.getvalue()
    assert "chunk=20" in normal.getvalue()
    assert "head=" not in normal.getvalue()
    assert payload.hex() not in normal.getvalue()
    assert payload.hex() in full.getvalue()


def test_missing_input_path_is_only_in_trace(tmp_path):
    reporter, normal, full = logs()
    path = str(tmp_path / "private-customer-document.txt")
    builder = PrintJobBuilder(PrinterCatalog.load().device_from_profile("v5x"), reporter=reporter)
    with pytest.raises(FileNotFoundError) as error:
        builder.build_from_file(path)
    reporter.error(exc=error.value)
    assert "Input file not found" in normal.getvalue()
    assert path not in normal.getvalue()
    assert "private-customer-document" not in normal.getvalue()
    assert path in full.getvalue()
