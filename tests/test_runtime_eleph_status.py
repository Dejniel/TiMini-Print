from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from tests.test_printing_send import _Reporter
from tests.test_runtime_eleph import Session, information
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolStep
from timiniprint.protocol.families.eleph_control import (
    DEVICE_INFO_QUERY, STATUS_QUERY, REPRINT_PROMPT, ElephReplyDecoder, ElephStatus,
)
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.status import PrinterStatusCode
from timiniprint.protocol.types import ImageEncoding


class Connection(Session):
    def __init__(self, status, *, mode=2, ble=False, query_available=True):
        super().__init__(
            {DEVICE_INFO_QUERY: information(mode=mode), STATUS_QUERY: status},
            ble=ble, query_available=query_available,
        )
        self.jobs = []
        self.payloads = []
        self.notifications = []

    def can_send_standard_payload(self):
        return True

    def _notify(self):
        for payload in self.notifications:
            self.attached.handle_notification(self, payload)

    async def send(self, job):
        self.jobs.append(job)
        self._notify()

    async def send_standard_payload(self, payload):
        self.payloads.append(payload)
        self._notify()


class ChunkedConnection(Connection):
    """Deliver whole transport chunks, including events coalesced with replies."""

    def __init__(self, status, *, mode=2, ble=False, handoff_notification=b""):
        super().__init__(status, mode=mode, ble=ble)
        self.handoff_notification = handoff_notification
        self.bootstrap = None

    async def attach_runtime_controller(self, controller, *, timeout):
        if self.attached is not None:
            self.bootstrap = self.attached
            if self.handoff_notification:
                self.attached.handle_notification(self, self.handoff_notification)
        await super().attach_runtime_controller(controller, timeout=timeout)

    async def _query(self, packet, complete):
        response = self.response[packet]
        if not isinstance(response, list):
            return await super()._query(packet, complete)
        self.sent.append(packet)
        buffer = bytearray()
        for fragment in response:
            buffer.extend(fragment)
            self.attached.handle_notification(self, fragment)
            candidate = fragment if self.ble else bytes(buffer)
            if complete(candidate):
                return candidate
        return None if self.ble else bytes(buffer) or None


@pytest.mark.parametrize("raw,codes", [
    (b"\x00\x00\x00\x00", ()),
    (b"\x20\x00\x00\x00", (PrinterStatusCode.COVER_OPEN,)),
    (b"\x00\x40\x00\x00", (PrinterStatusCode.OVERHEATED,)),
    (b"\x00\x08\x00\x00", (PrinterStatusCode.CUTTER_ERROR,)),
    (b"\x00\x00\x0c\x00", (PrinterStatusCode.PAPER_OUT,)),
    (b"\x00\x00\x04\x00", ()),
    (b"\x00\x00\x08\x00", ()),
    (b"\x00\x00\x00\xff", ()),
    (b"\x20\x48\x0c\x00", (PrinterStatusCode.COVER_OPEN, PrinterStatusCode.PAPER_OUT,
                               PrinterStatusCode.OVERHEATED, PrinterStatusCode.CUTTER_ERROR)),
])
def test_status_masks(raw, codes):
    assert ElephStatus.parse(raw).error_codes == codes



@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("reprint", [False, True])
@pytest.mark.parametrize("raw", [bytes(4), b"\x00\x00\x0c\x00"])
def test_framed_status_does_not_parse_err_prefix_as_status_bytes(ble, reprint, raw):
    reply = (REPRINT_PROMPT if reprint else b"") + b"err:" + raw
    connection = Connection(reply, ble=ble)

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        if raw[2]:
            with pytest.raises(PrinterNotReadyError) as caught:
                await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"))
            assert caught.value.reasons == (PrinterStatusCode.PAPER_OUT,)
        else:
            await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"))
            assert len(connection.jobs) == 1
        assert prepared.runtime_controller.debug_snapshot()["reprint_requested"] is reprint
        assert connection.sent == [DEVICE_INFO_QUERY, STATUS_QUERY]
    asyncio.run(scenario())


def test_notification_parser_requires_complete_frames_and_bounds_its_buffer():
    decoder = ElephReplyDecoder()
    stream = b"noise" + b"err:\x20\x48\x0c\x00" + REPRINT_PROMPT + b"err:\x00\x00\x00\x00"
    frames = []
    for value in stream:
        frames.extend(decoder.feed(bytes([value])))
    assert frames == [ElephStatus.parse(b"\x20\x48\x0c\x00"),
                      REPRINT_PROMPT, ElephStatus.parse(bytes(4))]
    assert decoder.feed(b"err:\x20\x48\x0c") == ()
    assert decoder.feed(b"\x00") == (ElephStatus.parse(b"\x20\x48\x0c\x00"),)
    assert decoder.feed(bytes(10000) + REPRINT_PROMPT) == (REPRINT_PROMPT,)


@pytest.mark.parametrize("packet,reply,record,events", [
    (STATUS_QUERY, bytes(4), bytes(4), b""),
    (STATUS_QUERY, b"err:\x20\x00\x00\x00", b"\x20\x00\x00\x00", b""),
    (STATUS_QUERY, REPRINT_PROMPT + bytes(4), bytes(4), REPRINT_PROMPT),
    (STATUS_QUERY, REPRINT_PROMPT * 2 + b"err:" + bytes(4), bytes(4), REPRINT_PROMPT * 2),
    (STATUS_QUERY, REPRINT_PROMPT, None, REPRINT_PROMPT),
    (STATUS_QUERY, b"err:\x20\x00", None, b"err:\x20\x00"),
    (STATUS_QUERY, bytes(3), None, b""),
    (STATUS_QUERY, bytes(4) + b"err:\x20", bytes(4), b"err:\x20"),
    (DEVICE_INFO_QUERY, information() + b"err:", information(), b"err:"),
    (DEVICE_INFO_QUERY, information()[:-1], None, b""),
])
def test_query_decoder_separates_owned_record_and_events(packet, reply, record, events):
    decoder = ElephReplyDecoder()
    assert decoder.query_reply(packet, reply) == (record, events)
    # Matchers can inspect accumulated data repeatedly without consuming it.
    assert decoder.query_reply(packet, reply) == (record, events)


@pytest.mark.parametrize("ble", [False, True])
def test_reprint_without_status_is_reported_but_does_not_block_send_only_printing(ble):
    connection = Connection(REPRINT_PROMPT, ble=ble)
    reporter = _Reporter()

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"), reporter=reporter)
        snapshot = prepared.runtime_controller.debug_snapshot()
        assert snapshot["reprint_requested"] is True
        assert snapshot["query_disabled_reason"] == "missing or incomplete reply"
        assert snapshot["status"] is None
        assert len(connection.jobs) == 1
        assert [short for short, detail in reporter.warnings] == ["Eleph reprint requested"]
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("initial_mode", [1, 2])
@pytest.mark.parametrize("during_handoff", [False, True])
@pytest.mark.parametrize("event,split", [
    (event, split)
    for event in (b"err:\x20\x00\x00\x00", REPRINT_PROMPT)
    for split in range(1, len(event))
])
def test_family_change_preserves_fragmented_events(ble, initial_mode, during_handoff, event, split):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    if initial_mode == 1:
        device = replace(device, protocol_family=ProtocolFamily.ELEPH_ESC).with_print_profile(
            device.profile,
            image_pipeline=replace(device.image_pipeline, encoding=ImageEncoding.ELEPH_ESC_RAW),
        )
    reported_mode = 3 - initial_mode
    connection = ChunkedConnection(
        bytes(4), ble=ble,
        handoff_notification=event[split:] if during_handoff else b"",
    )
    connection.response[DEVICE_INFO_QUERY] = [information(mode=reported_mode) + event[:split]]

    async def scenario():
        prepared = await prepare_connection_runtime(device, connection)
        if not during_handoff:
            connection.attached.handle_notification(connection, event[split:])
        controller = prepared.runtime_controller
        assert controller is not connection.bootstrap
        assert connection.attached is controller
        family = ProtocolFamily.ELEPH_ESC if reported_mode == 1 else ProtocolFamily.ELEPH_TSPL
        assert prepared.device.protocol_family is family
        assert prepared.device.ble_transport_profile == device.ble_transport_profile
        assert prepared.device.profile.stream == device.profile.stream
        snapshot = controller.debug_snapshot()
        assert snapshot == connection.bootstrap.debug_snapshot()
        if event == REPRINT_PROMPT:
            assert snapshot["reprint_requested"] is True
            assert [warning["short"] for warning in connection.warnings] == ["Eleph reprint requested"]
        else:
            assert snapshot["status"] == "20000000"
            assert snapshot["status_errors"] == ["cover_open"]
            assert not connection.warnings
        assert connection.sent == [DEVICE_INFO_QUERY]
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("mode", [1, 2])
@pytest.mark.parametrize("steps", [False, True])
def test_status_query_records_owned_reply_before_coalesced_fault(ble, mode, steps):
    connection = ChunkedConnection([bytes(4) + b"err:\x20\x00\x00\x00"], mode=mode, ble=ble)
    job = ProtocolJob(payload=b"image", steps=(ProtocolStep.send("image", b"image"),) if steps else ())

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        with pytest.raises(PrinterNotReadyError) as caught:
            await send_prepared_job(prepared, connection, job)
        assert caught.value.reasons == (PrinterStatusCode.COVER_OPEN,)
        assert prepared.runtime_controller.debug_snapshot()["status_errors"] == ["cover_open"]
        assert connection.jobs == connection.payloads == []
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("mode", [1, 2])
@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("wait", [False, True])
def test_fresh_status_blocks_both_send_paths_before_print_bytes(ble, mode, steps, wait):
    connection = Connection(b"\x00\x00\x0c\x00", mode=mode, ble=ble)
    job = ProtocolJob(payload=b"image", steps=(ProtocolStep.send("image", b"image"),) if steps else (),
                      wait_for_completion=wait)

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        with pytest.raises(PrinterNotReadyError) as caught:
            await send_prepared_job(prepared, connection, job)
        assert caught.value.reasons == (PrinterStatusCode.PAPER_OUT,)
        assert connection.jobs == connection.payloads == []
        # Fixing the paper lets the next print proceed; errors are scoped to one job.
        connection.response[STATUS_QUERY] = bytes(4)
        await send_prepared_job(prepared, connection, job)
        assert len(connection.jobs if not steps else connection.payloads) == 1
        assert connection.sent == [DEVICE_INFO_QUERY, STATUS_QUERY, STATUS_QUERY]
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("mode", [1, 2])
@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("wait", [False, True])
def test_fragmented_passive_fault_is_reported_even_without_completion_wait(ble, mode, steps, wait):
    connection = Connection(bytes(4), mode=mode, ble=ble)
    connection.notifications = [b"e", b"rr:\x20", b"\x00\x00", b"\x00", b"err:" + bytes(4)]
    job = ProtocolJob(payload=b"image", steps=(ProtocolStep.send("image", b"image"),) if steps else (),
                      wait_for_completion=wait)

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        with pytest.raises(PrinterNotReadyError) as caught:
            await send_prepared_job(prepared, connection, job)
        assert caught.value.reasons == (PrinterStatusCode.COVER_OPEN,)
        assert len(connection.jobs if not steps else connection.payloads) == 1
        assert prepared.runtime_controller.debug_snapshot()["status_errors"] == []
        # A later healthy notification cannot turn an interrupted print into success.
        await prepared.runtime_controller.before_write(connection, size=1, timeout=1)
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("reply", [None, b"\x00", b"\x00\x00\x00"])
def test_optional_missing_status_disables_queries_not_printing(ble, reply):
    connection = Connection(reply, ble=ble)

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"))
        connection.response[STATUS_QUERY] = b"\x20\x48\x0c\x00"  # late/unowned reply
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"second"))
        assert connection.sent == [DEVICE_INFO_QUERY, STATUS_QUERY]
        assert len(connection.jobs) == 2
        assert prepared.runtime_controller.debug_snapshot()["query_disabled_reason"]
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
def test_missing_info_does_not_let_late_info_become_status(ble):
    connection = Connection(information(), ble=ble)
    connection.response[DEVICE_INFO_QUERY] = None

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"))
        assert connection.sent == [DEVICE_INFO_QUERY]
        assert len(connection.jobs) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
def test_reprint_prompt_is_diagnostic_not_automatic_replay(ble):
    connection = Connection(bytes(4), ble=ble)
    connection.notifications = [REPRINT_PROMPT[:1], REPRINT_PROMPT[1:]]

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        await send_prepared_job(prepared, connection, ProtocolJob(payload=b"image"))
        assert len(connection.jobs) == 1
        assert connection.sent == [DEVICE_INFO_QUERY, STATUS_QUERY]
        assert connection.warnings[-1]["short"] == "Eleph reprint requested"
        assert prepared.runtime_controller.debug_snapshot()["reprint_requested"] is True
    asyncio.run(scenario())


@pytest.mark.parametrize("ble", [False, True])
def test_diagnostic_info_reads_do_not_mutate_prepared_device(ble):
    connection = Connection(b"\x20\x48\x0c\x00", mode=1, ble=ble)

    async def scenario():
        prepared = await prepare_connection_runtime(
            PrinterCatalog.load().device_from_model("eleph_tspl_p1"), connection,
        )
        device = prepared.device
        controller = prepared.runtime_controller
        assert controller.diagnostic_fields(device) == ("device_info", "status")
        connection.response[DEVICE_INFO_QUERY] = information(width=384, mode=2)
        info = await controller.query_diagnostic(device, connection, "device_info", timeout=1)
        status = await controller.query_diagnostic(device, connection, "status", timeout=1)
        assert info["width"] == 384 and info["command_mode"] == 2
        assert status == {"raw": "20480c00", "errors": [
            "cover_open", "paper_out", "overheated", "cutter_error",
        ]}
        assert prepared.device is device
        assert device.profile.default_paper_preset.paper_width_px == 576
        assert controller.debug_snapshot()["device_info"]["command_mode"] == 1
        with pytest.raises(ValueError, match="diagnostic field"):
            await controller.query_diagnostic(device, connection, "write_mode", timeout=1)
        assert connection.sent == [DEVICE_INFO_QUERY, DEVICE_INFO_QUERY, STATUS_QUERY]
    asyncio.run(scenario())
