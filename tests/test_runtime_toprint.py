import asyncio

import pytest

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing import PrinterNotReadyError
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.runtime.toprint import ToPrintRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import PrinterStatusCode, ProtocolJob, ProtocolStep
from timiniprint.protocol.families.toprint_control import ToPrintReplyDecoder, ToPrintStatus


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


class Connection:
    def __init__(self, replies=(), *, steps_available=True, write_hook=False):
        self.controller = None
        self.replies = iter(replies)
        self.sent = []
        self.write_hook = write_hook
        if not steps_available:
            self.send_standard_payload = None

    async def attach_runtime_controller(self, controller, *, timeout):
        self.controller = controller

    async def send(self, job):
        await self._write(job.payload)

    async def send_standard_payload(self, payload):
        await self._write(payload)

    async def _write(self, payload):
        session = RuntimeConnectionSession(self, reporter=reporting.DUMMY_REPORTER)
        if self.write_hook:
            await self.controller.before_write(session, size=len(payload), timeout=1)
        self.sent.append(payload)
        reply = next(self.replies, None)
        if reply is not None:
            self.controller.handle_notification(session, reply)


@pytest.mark.parametrize("raw,errors", [
    (0x00, ()), (0x01, ("paper_out",)), (0x02, ("cover_open",)),
    (0x03, ("paper_out", "cover_open")), (0x04, ()),
    (0x05, ("paper_out", "low_battery")), (0x07, ("paper_out", "cover_open", "low_battery")),
    (0x10, ()), (0x20, ()), (0x30, ()), (0x80, ()),
])
def test_status_uses_documented_composite_mask_without_invented_overheat_bit(raw, errors):
    status = ToPrintStatus(raw)
    assert status.errors == errors
    assert status.printing == bool(raw & 0x20)
    assert status.paused == bool(raw & 0x10)


def test_mode_replies_and_coalesced_status_bytes_are_not_confused():
    decoder = ToPrintReplyDecoder()
    assert decoder.feed(b"\xaa") == ()
    assert decoder.feed(b"\x01\x20\xaa\x03\x00") == (
        b"\xaa\x01", ToPrintStatus(0x20), b"\xaa\x03", ToPrintStatus(0),
    )
    assert decoder.feed(b"\x01\x02") == (ToPrintStatus(1), ToPrintStatus(2))
    assert decoder.feed(b"\xaa\x7f") == (b"\xaa\x7f",)


@pytest.mark.parametrize("profile", ["toprint_tspl_p1", "toprint_hprt_esc_zl1"])
@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("steps_available", [False, True])
def test_passive_runtime_sends_without_queries_flow_control_or_completion_ack(catalog, profile, steps, steps_available):
    connection = Connection([b"\xaa\x01\x20\x10", b"\0"], steps_available=steps_available)
    job = ProtocolJob(payload=b"AB", wait_for_completion=True,
                      steps=(ProtocolStep.send("A", b"A"), ProtocolStep.send("B", b"B")) if steps else ())
    async def run():
        device = catalog.device_from_profile(profile)
        prepared = await prepare_connection_runtime(device, connection)
        assert isinstance(prepared.runtime_controller, ToPrintRuntimeController)
        await send_prepared_job(prepared, connection, job)
    asyncio.run(run())
    assert connection.sent == ([b"A", b"B"] if steps and steps_available else [b"AB"])
    assert connection.controller.debug_snapshot()["mode"] == "aa01"


@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("steps_available", [False, True])
@pytest.mark.parametrize("write_hook", [False, True])
def test_latched_error_aborts_active_job_even_when_followed_by_ready(catalog, steps, steps_available, write_hook):
    connection = Connection([b"\x01\0"], steps_available=steps_available, write_hook=write_hook)
    job = ProtocolJob(payload=b"AB", wait_for_completion=True,
                      steps=(ProtocolStep.send("A", b"A"), ProtocolStep.send("B", b"B")) if steps else ())
    async def run():
        prepared = await prepare_connection_runtime(catalog.device_from_profile("toprint_tspl_p1"), connection)
        with pytest.raises(PrinterNotReadyError) as caught:
            await send_prepared_job(prepared, connection, job)
        assert caught.value.reasons == (PrinterStatusCode.PAPER_OUT,)
        # The clear status permits a new explicit attempt, not a retry of the failed job.
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"C"))
    asyncio.run(run())
    assert connection.sent == ([b"A", b"C"] if steps and steps_available else [b"AB", b"C"])


def test_existing_fault_is_checked_before_any_job_bytes(catalog):
    controller = runtime_controller_for_device(catalog.device_from_profile("toprint_hprt_esc_zl1"))
    connection = Connection()
    async def run():
        prepared = await prepare_connection_runtime(catalog.device_from_profile("toprint_hprt_esc_zl1"),
                                                    connection, controller=controller)
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        controller.handle_notification(session, b"\x02")
        with pytest.raises(PrinterNotReadyError) as caught:
            await send_prepared_job(prepared, connection, ProtocolJob(payload=b"A"))
        assert caught.value.reasons == (PrinterStatusCode.COVER_OPEN,)
        assert not connection.sent
        controller.handle_notification(session, b"\0")
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"B"))
    asyncio.run(run())
    assert connection.sent == [b"B"]


def test_status_while_idle_is_visible_but_does_not_latch_a_cleared_error(catalog):
    controller = runtime_controller_for_device(catalog.device_from_profile("toprint_tspl_p1"))
    connection = Connection()
    session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
    controller.handle_notification(session, b"\x05")
    assert controller.debug_snapshot()["errors"] == ["paper_out", "low_battery"]
    controller.handle_notification(session, b"\0")
    assert controller.debug_snapshot()["errors"] == []
    asyncio.run(controller.before_write(session, size=8, timeout=1))
