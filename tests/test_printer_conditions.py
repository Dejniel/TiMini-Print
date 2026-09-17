from __future__ import annotations

import asyncio
import json
import pickle
from concurrent.futures import Future
from copy import copy, deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing import PrinterNotReadyError
from timiniprint.printing.connected import ConnectedPrinter
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.phomemo_esc import PhomemoEscRuntimeController
from timiniprint.protocol import PrinterStatusCode, ProtocolJob, ProtocolStep


def test_device_error_contract_preserves_detail_and_unique_serializable_reasons():
    error = PrinterNotReadyError(
        "Printer says: cover open and paper out", PrinterStatusCode.COVER_OPEN,
        PrinterStatusCode.PAPER_OUT, PrinterStatusCode.COVER_OPEN,
    )
    assert str(error) == error.detail
    assert error.reasons == (PrinterStatusCode.COVER_OPEN, PrinterStatusCode.PAPER_OUT)
    assert json.loads(json.dumps(error.reasons)) == ["cover_open", "paper_out"]
    assert not isinstance(error, (RuntimeError, OSError))


@pytest.mark.parametrize("clone", [copy, deepcopy, pytest.param(
    lambda error: pickle.loads(pickle.dumps(error)), id="pickle",
)])
def test_device_error_survives_copy_and_serialization(clone):
    error = PrinterNotReadyError(
        "Paper out; cover open", PrinterStatusCode.PAPER_OUT,
        PrinterStatusCode.COVER_OPEN, PrinterStatusCode.PAPER_OUT,
    )
    restored = clone(error)
    assert type(restored) is PrinterNotReadyError
    assert str(restored) == restored.detail == error.detail
    assert restored.reasons == error.reasons


class ReplyConnection:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.disconnected = False

    async def send(self, job):
        self.sent.append(job.payload)

    async def send_standard_payload(self, data):
        self.sent.append(data)

    def can_wait_for_reply(self):
        return True

    async def wait_for_reply(self, label, match, *, timeout, required=True):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        assert match(reply)
        return reply

    async def disconnect(self):
        self.disconnected = True


@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("reply,reason", [
    (b"\x1a\x03\xa9", PrinterStatusCode.OVERHEATED),
    (b"\x1a\x05\x99", PrinterStatusCode.COVER_OPEN),
    (b"\x1a\x06\x88", PrinterStatusCode.PAPER_OUT),
    (b"\x1a\x0f\x01", PrinterStatusCode.PRINTER_ERROR),
])
def test_connected_print_reports_device_condition_and_allows_explicit_later_print(steps, reply, reason):
    connection = ReplyConnection([reply, b"\x1a\x0f\x0c"])
    context = PreparedPrinter(PrinterCatalog.load().device_from_model("printmaster_m110"), runtime_controller=PhomemoEscRuntimeController())
    printer = ConnectedPrinter(connection, context)
    job = ProtocolJob(payload=b"raster", wait_for_completion=True,
                      steps=(ProtocolStep.send("raster", b"raster"),) if steps else ())

    async def run():
        with pytest.raises(PrinterNotReadyError) as caught:
            await printer.send_job(job)
        assert caught.value.reasons == (reason,)
        assert connection.sent == [b"raster"]  # No implicit retry.
        assert not connection.disconnected
        await printer.send_job(job)
        assert connection.sent == [b"raster", b"raster"]
        await printer.disconnect()
        assert connection.disconnected

    asyncio.run(run())


@pytest.mark.parametrize("failure", [TimeoutError("no reply"), OSError("disconnected"), asyncio.CancelledError()])
def test_transport_failure_and_cancellation_are_not_reclassified(failure):
    connection = ReplyConnection([failure])
    printer = ConnectedPrinter(connection, PreparedPrinter(PrinterCatalog.load().device_from_model('printmaster_m110'), runtime_controller=PhomemoEscRuntimeController()))
    with pytest.raises(type(failure)) as caught:
        asyncio.run(printer.send_job(ProtocolJob(payload=b"raster", wait_for_completion=True)))
    assert caught.value is failure


@pytest.mark.parametrize("operation", [reporting.ERROR_PRINT_FAILED, reporting.ERROR_PAPER_MOTION_FAILED,
                                       reporting.ERROR_CONNECT_FAILED])
def test_gui_reports_attention_instead_of_a_program_or_connection_failure(operation):
    from timiniprint.app.gui import TiMiniPrintGUI

    gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
    gui.reporter, sink = build_capture_reporter()
    error = PrinterNotReadyError("Out of paper", PrinterStatusCode.PAPER_OUT)
    gui._queue_error(operation, exc=error)
    message, = sink.messages
    assert message.level == "warning"
    assert message.key == reporting.WARNING_PRINTER_NOT_READY
    assert message.context["reasons"] == ("paper_out",)
    assert message.short == "Printer needs attention: Out of paper"
    assert message.exc is error


@pytest.mark.parametrize("action", ["feed", "retract"])
@pytest.mark.parametrize("release_before_completion", [False, True])
@pytest.mark.parametrize("failure", [
    None,
    PrinterNotReadyError("Out of paper", PrinterStatusCode.PAPER_OUT),
    OSError("Connection lost"),
], ids=["success", "printer-condition", "connection-failure"])
def test_gui_paper_motion_keeps_failure_visible_after_stopping(action, release_before_completion, failure):
    from timiniprint.app.gui import TiMiniPrintGUI

    gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
    gui.reporter, sink = build_capture_reporter()
    gui.connected_device = object()
    connected = SimpleNamespace(feed=AsyncMock(side_effect=failure), retract=AsyncMock(side_effect=failure))
    gui.connected_printer = connected
    gui._paper_motion_action = action
    gui._paper_motion_busy = False
    gui._paper_motion_job = "repeat-timer"
    gui.after_cancel = Mock()
    gui.ble_loop = SimpleNamespace(submit=Mock())

    gui._send_paper_motion(action)
    args, kwargs = gui.ble_loop.submit.call_args
    if release_before_completion:
        gui._stop_paper_motion()
    future = Future()
    try:
        future.set_result(asyncio.run(args[0]))
    except Exception as exc:
        future.set_exception(exc)
    kwargs["callback"](future)
    if failure is not None:
        # Completion already stopped repetition; releasing the button must
        # not replace the failure with a misleading "Connected" status.
        assert gui._paper_motion_action is None
    gui._stop_paper_motion()

    expected_key = (
        reporting.STATUS_CONNECT_DONE if failure is None else
        reporting.WARNING_PRINTER_NOT_READY if isinstance(failure, PrinterNotReadyError) else
        reporting.ERROR_PAPER_MOTION_FAILED
    )
    assert sink.messages[-1].key == expected_key
    assert gui.connected_printer is connected
    assert not gui._paper_motion_busy
    assert gui._paper_motion_job is None
    gui.after_cancel.assert_called_once_with("repeat-timer")
    getattr(connected, action).assert_awaited_once()


def test_cli_device_condition_is_actionable_but_still_unsuccessful():
    from timiniprint.app import cli

    reporter, sink = build_capture_reporter()
    error = PrinterNotReadyError("Out of paper", PrinterStatusCode.PAPER_OUT)
    with patch.object(cli, "_build_cli_reporter", return_value=reporter), \
         patch.object(cli, "emit_startup_warnings"), patch.object(cli, "emit_update_warning"), \
         patch.object(cli, "print_bluetooth", side_effect=error):
        assert cli.main(["--text", "test"]) == 2
    message = sink.messages[-1]
    assert message.key == reporting.WARNING_PRINTER_NOT_READY
    assert message.context["reasons"] == ("paper_out",)
    assert message.exc is error
