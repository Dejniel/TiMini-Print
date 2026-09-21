from __future__ import annotations

from contextlib import contextmanager

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import pytest

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.phomemo import PhomemoRuntimeController
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import PaperMode, ProtocolJob, PrinterStatusCode
from timiniprint.protocol.families.phomemo_esc.flow import PhomemoPageStep
from timiniprint.protocol.families.phomemo_esc.replies import PhomemoReplyDecoder, phomemo_reply



@contextmanager
def simulated_delays():
    now = [0.0]

    async def advance(delay):
        now[0] += delay

    with patch("timiniprint.printing.runtime.phomemo.time") as clock:
        clock.monotonic.side_effect = lambda: now[0]
        with patch("timiniprint.printing.runtime.phomemo.asyncio.sleep", new_callable=AsyncMock, side_effect=advance) as sleep:
            yield sleep


class ReplyConnection:
    def __init__(self, replies=()):
        self.controller = None
        self.session = RuntimeConnectionSession(self, reporter=reporting.DUMMY_REPORTER)
        self.replies = list(replies)
        self.pending = bytearray()
        self.sent = []
        self.queries = []
        self.wait_replies = []
        self.query_replies = {
            bytes.fromhex("1f1112"): bytes.fromhex("1a0598"),
            bytes.fromhex("1f1111"): bytes.fromhex("1a0689"),
            bytes.fromhex("1f1146"): bytes.fromhex("1a2000"),
            bytes.fromhex("1f112f"): bytes.fromhex("1a1d00"),
        }

    async def attach_runtime_controller(self, controller, *, timeout):
        self.controller = controller

    def deliver(self, data):
        if self.controller is not None:
            self.controller.handle_notification(self.session, data)
        self.pending.extend(data)

    async def send_standard_payload(self, data):
        self.sent.append(bytes(data))
        if self.replies:
            for part in self.replies.pop(0):
                self.deliver(part)

    async def send(self, job):
        await self.send_standard_payload(job.payload)

    def can_wait_for_reply(self):
        return True

    async def query_control_packet(self, data, *, timeout, reply_complete):
        self.queries.append(bytes(data))
        reply = self.query_replies.get(bytes(data))
        if reply:
            self.deliver(reply)
        return reply

    async def wait_for_reply(self, label, match, *, timeout, required):
        if self.pending and (match(bytes(self.pending)) or timeout == 0):
            data = bytes(self.pending)
            self.pending.clear()
            return data
        if timeout and self.wait_replies:
            self.deliver(self.wait_replies.pop(0))
            data = bytes(self.pending)
            self.pending.clear()
            return data
        await asyncio.sleep(min(max(0, timeout), 0.001))
        return None


class PhomemoDecoderTests(unittest.TestCase):
    def test_every_fragment_boundary_and_concatenated_frames(self):
        serial = b"Q171" + bytes.fromhex("1a0f0c") + b"12345678"
        data = bytes.fromhex("1a08") + serial + bytes.fromhex("1a3b0004480000 1a0f0c")
        for split in range(len(data) + 1):
            decoder = PhomemoReplyDecoder()
            frames = decoder.feed(data[:split]) + decoder.feed(data[split:])
            self.assertEqual(frames, [b"\x08" + serial, bytes.fromhex("3b0004480000"), bytes.fromhex("0f0c")])

    def test_no_bare_or_printmaster_reply_interpretation(self):
        self.assertEqual(PhomemoReplyDecoder().feed(bytes.fromhex("0f0c 1b0f0c")), [])
        self.assertEqual(phomemo_reply(bytes.fromhex("1a1d00"), 0x1D), b"\x00")
        self.assertIsNone(phomemo_reply(bytes.fromhex("1a2f00"), 0x1D))
        self.assertIsNone(phomemo_reply(bytes.fromhex("1a08") + b"Q171", 0x08))

    def test_latest_complete_requested_reply(self):
        self.assertEqual(phomemo_reply(bytes.fromhex("1a1d01 1a0598 1a1d00"), 0x1D), b"\x00")


class PhomemoRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sheet_readiness_applies_before_the_first_step_is_dispatched(self):
        connection = ReplyConnection()
        context = self.context(connection, paginated=False)
        step = PhomemoPageStep(label="sheet", data=b"raster", paginated=True, paper_mode=PaperMode.TATTOO)
        job = ProtocolJob(steps=(step,), wait_for_completion=True)
        async with context.runtime_controller.job_scope(connection.session, job, timeout=0.1):
            connection.deliver(bytes.fromhex("1a0688"))
            self.assertTrue(context.runtime_controller.debug_snapshot()["paper_out"])
        self.assertFalse(connection.sent)

    def context(self, connection, **options):
        controller = PhomemoRuntimeController(**options)
        controller.COMPLETION_TIMEOUT_SEC = 0.05
        connection.controller = controller
        device = PrinterCatalog.load().device_from_model("phomemo_m02")
        return PreparedPrinter(device, controller)

    async def test_direct_pages_wait_separately_even_for_early_fragmented_results(self):
        connection = ReplyConnection([[b"\x1a", b"\x0f\x0c"], [bytes.fromhex("1a0f0c")]])
        context = self.context(connection, paginated=True)
        steps = tuple(PhomemoPageStep(label="page", data=bytes([i]), paginated=True) for i in (1, 2))
        await send_prepared_job(context, connection, ProtocolJob(steps=steps, wait_for_completion=True))
        self.assertEqual(connection.sent, [b"\x01", b"\x02"])

    async def test_direct_page_failure_prevents_next_raster(self):
        for reply, status in (
            ("1a0f01", PrinterStatusCode.PRINTER_ERROR),
            ("1a0599", PrinterStatusCode.COVER_OPEN),
            ("1a0bb8", PrinterStatusCode.NOT_READY),
        ):
            connection = ReplyConnection([[bytes.fromhex(reply)]])
            context = self.context(connection, paginated=True)
            steps = tuple(PhomemoPageStep(label="page", data=bytes([i]), paginated=True) for i in (1, 2))
            with self.assertRaises(PrinterNotReadyError) as caught:
                await send_prepared_job(context, connection, ProtocolJob(steps=steps, wait_for_completion=True))
            self.assertEqual(caught.exception.reasons, (status,))
            self.assertEqual(len(connection.sent), 1)

    async def test_buffered_pages_query_once_and_count_every_result(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c1a0f0c")]])
        context = self.context(connection, paginated=True)
        steps = tuple(PhomemoPageStep(
            label="page", data=(b"setup" if i == 1 else b"") + bytes([i]), paginated=True, buffered=True,
        ) for i in (1, 2))
        await send_prepared_job(context, connection, ProtocolJob(steps=steps, wait_for_completion=True))
        self.assertEqual(connection.queries, [bytes.fromhex("1f112f")])
        self.assertEqual(connection.sent, [b"setup\x01\x02"])

    async def test_buffered_missing_one_result_does_not_complete_document(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
        context = self.context(connection, paginated=True)
        steps = tuple(PhomemoPageStep(label="page", data=b"x", paginated=True, buffered=True) for _ in range(2))
        with self.assertRaisesRegex(RuntimeError, "completion timed out"):
            await send_prepared_job(context, connection, ProtocolJob(steps=steps, wait_for_completion=True))

    async def test_buffered_busy_wrong_opcode_and_absent_reply_never_send(self):
        for reply in (bytes.fromhex("1a1d01"), bytes.fromhex("1a2f00"), None):
            connection = ReplyConnection()
            connection.query_replies[bytes.fromhex("1f112f")] = reply
            context = self.context(connection, paginated=True)
            step = PhomemoPageStep(label="page", data=b"x", paginated=True, buffered=True)
            with self.assertRaises((RuntimeError, PrinterNotReadyError)):
                await send_prepared_job(context, connection, ProtocolJob(steps=(step,), wait_for_completion=True))
            self.assertFalse(connection.sent)

    async def test_raw_payload_and_steps_both_enforce_ribbon_fault(self):
        for use_steps in (False, True):
            connection = ReplyConnection([[bytes.fromhex("1a2001 1a0f0c")]])
            context = self.context(connection, ribbon_status=True, paginated=True)
            job = (ProtocolJob(steps=(PhomemoPageStep(label="page", data=b"x", paginated=True),), wait_for_completion=True)
                   if use_steps else ProtocolJob(payload=b"x", wait_for_completion=True))
            with self.assertRaises(PrinterNotReadyError) as caught:
                await send_prepared_job(context, connection, job)
            self.assertEqual(caught.exception.reasons, (PrinterStatusCode.RIBBON_ERROR,))

    async def test_stale_completion_cannot_finish_next_send_or_partial_old_frame(self):
        for previous in (bytes.fromhex("1a0f0c"), bytes.fromhex("1a0f")):
            connection = ReplyConnection([[b"\x0c"]])
            context = self.context(connection, paginated=True)
            connection.deliver(previous)
            with self.assertRaisesRegex(RuntimeError, "completion timed out"):
                await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))

    async def test_cutter_interrupts_tape_job(self):
        connection = ReplyConnection([[bytes.fromhex("1a0eb8 1a0f0c")]])
        context = self.context(connection, cutter_status=True)
        with self.assertRaises(PrinterNotReadyError) as caught:
            await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))
        self.assertEqual(caught.exception.reasons, (PrinterStatusCode.CUTTER_ERROR,))

    async def test_page_waits_for_paper_and_then_continues(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
        context = self.context(connection, paginated=True)
        connection.deliver(bytes.fromhex("1a0688"))
        connection.wait_replies = [bytes.fromhex("1a0689")]
        step = PhomemoPageStep(label="page", data=b"x", paginated=True)
        await send_prepared_job(context, connection, ProtocolJob(steps=(step,), wait_for_completion=True))
        self.assertEqual(connection.sent, [b"x"])

    async def test_compact_timer_does_not_require_physical_ack_but_honors_failure(self):
        for replies, fails in (([], False), ([[bytes.fromhex("1a0f01")]], True)):
            connection = ReplyConnection(replies)
            context = self.context(connection)
            step = PhomemoPageStep(label="page", data=b"x", completion_delay_sec=0.002)
            job = ProtocolJob(steps=(step,), wait_for_completion=True)
            if fails:
                with self.assertRaises(PrinterNotReadyError):
                    await send_prepared_job(context, connection, job)
            else:
                await send_prepared_job(context, connection, job)

    async def test_m03_cancellation_exception_and_unknown_statuses(self):
        connection = ReplyConnection([[bytes.fromhex("1a0bb8 1a0500 1a0300 1a0f0c")]])
        context = self.context(connection, ignore_cancel=True)
        await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))

    async def test_failed_send_releases_scope_for_next_attempt(self):
        connection = ReplyConnection()
        context = self.context(connection)
        original_send = connection.send
        async def broken(job):
            raise OSError("send failed")
        connection.send = broken
        with self.assertRaises(OSError):
            await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))
        connection.send = original_send
        connection.replies = [[bytes.fromhex("1a0f0c")]]
        await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))

    async def test_non_observing_connection_handles_raw_and_page_completion(self):
        class PassiveConnection(ReplyConnection):
            attach_runtime_controller = None

            def deliver(self, data):
                self.pending.extend(data)

        for use_steps in (False, True):
            connection = PassiveConnection([[bytes.fromhex("1a03a8 1a0f0c")]])
            context = self.context(connection, paginated=True)
            connection.pending.extend(bytes.fromhex("1a0f0c"))
            job = (ProtocolJob(steps=(PhomemoPageStep(label="page", data=b"x", paginated=True),), wait_for_completion=True)
                   if use_steps else ProtocolJob(payload=b"x", wait_for_completion=True))
            await send_prepared_job(context, connection, job)
            self.assertEqual(connection.sent, [b"x"])

    async def test_atomic_notification_query_reassembles_fragments_without_spp(self):
        class NotificationConnection(ReplyConnection):
            def can_query_control_packet(self):
                return False

            def can_send_control_packet_wait_notification(self):
                return True

            async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
                self.queries.append(packet)
                for part in (b"\x1a", b"\x20", b"\x00"):
                    self.deliver(part)
                    if match(part):
                        return part
                raise AssertionError("Incomplete query")

        connection = NotificationConnection()
        context = self.context(connection, ribbon_status=True)
        reply = await context.runtime_controller.query_reply(
            connection.session, bytes.fromhex("1f1146"), 0x20, timeout=0.05,
        )
        self.assertEqual(reply, b"\x00")
        self.assertEqual(connection.queries, [bytes.fromhex("1f1146")])

    async def test_sheet_cooldowns_do_not_delay_roll_and_mark_media(self):
        for medium in (PaperMode.A4_SHEET, PaperMode.PLAIN, PaperMode.TAG):
            for recovered in (False, True):
                connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
                context = self.context(connection, paginated=True, paper_recovery_delay_sec=3.0)
                context.runtime_controller.COMPLETION_TIMEOUT_SEC = 10.0
                step = PhomemoPageStep(label="page", data=b"x", paginated=True, paper_mode=medium)
                with simulated_delays() as sleep:
                    if recovered:
                        connection.deliver(bytes.fromhex("1a0688"))
                    connection.deliver(bytes.fromhex("1a0689"))
                    # Duplicate present reports must not erase a recovery cooldown.
                    connection.deliver(bytes.fromhex("1a0689"))
                    await send_prepared_job(context, connection, ProtocolJob(steps=(step,), wait_for_completion=True))
                if recovered and medium is PaperMode.A4_SHEET:
                    sleep.assert_awaited_once()
                    self.assertGreater(sleep.await_args.args[0], 2.0)
                else:
                    sleep.assert_not_awaited()

    async def test_paper_present_delay_does_not_require_previous_paper_error(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
        context = self.context(connection, paginated=True, paper_present_delay_sec=2.0)
        context.runtime_controller.COMPLETION_TIMEOUT_SEC = 10.0
        with simulated_delays() as sleep:
            connection.deliver(bytes.fromhex("1a0689"))
            await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))
        sleep.assert_awaited_once()
        self.assertGreater(sleep.await_args.args[0], 1.0)

    async def test_busy_state_is_not_the_only_readiness_check_after_idle_query(self):
        connection = ReplyConnection()
        context = self.context(connection, paginated=True)
        connection.query_replies[bytes.fromhex("1f112f")] = bytes.fromhex("1a1d00 1a0599")
        step = PhomemoPageStep(label="page", data=b"x", paginated=True, buffered=True)
        with self.assertRaises(PrinterNotReadyError) as caught:
            await send_prepared_job(context, connection, ProtocolJob(steps=(step,), wait_for_completion=True))
        self.assertEqual(caught.exception.reasons, (PrinterStatusCode.COVER_OPEN,))
        self.assertFalse(connection.sent)

    async def test_overheat_during_cooldown_prevents_sending_until_cooled(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
        context = self.context(connection, paginated=True, paper_present_delay_sec=2.0)
        context.runtime_controller.COMPLETION_TIMEOUT_SEC = 10.0
        connection.wait_replies = [bytes.fromhex("1a03a8")]
        with simulated_delays() as sleep:
            advance = sleep.side_effect
            async def overheat_during_sleep(delay):
                await advance(delay)
                connection.deliver(bytes.fromhex("1a03a9"))
            sleep.side_effect = overheat_during_sleep
            connection.deliver(bytes.fromhex("1a0689"))
            await send_prepared_job(context, connection, ProtocolJob(payload=b"x", wait_for_completion=True))
        self.assertEqual(connection.wait_replies, [])
        self.assertEqual(connection.sent, [b"x"])


@pytest.mark.parametrize("path", ["payload", "direct", "buffered"])
@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("fragmented", [False, True])
@pytest.mark.parametrize("blocked,ready", [("1a03a9", "1a03a8"), ("1a0688", "1a0689")])
def test_results_received_before_readiness_cannot_finish_an_unsent_page(path, observed, fragmented, blocked, ready):
    class PassiveConnection(ReplyConnection):
        attach_runtime_controller = None

        def deliver(self, data):
            self.pending.extend(data)

    async def run():
        connection = (ReplyConnection if observed else PassiveConnection)([[b"\x0c"]] if fragmented else [])
        controller = PhomemoRuntimeController(paginated=True)
        controller.COMPLETION_TIMEOUT_SEC = 0.05
        connection.controller = controller
        context = PreparedPrinter(PrinterCatalog.load().device_from_model("phomemo_m02"), controller)
        if path == "buffered":
            # Readiness must be rechecked after the buffer-idle reply as well.
            connection.query_replies[bytes.fromhex("1f112f")] = bytes.fromhex("1a1d00" + blocked)
        else:
            connection.deliver(bytes.fromhex(blocked))
        stale_result = bytes.fromhex("1a0f" if fragmented else "1a0f0c")
        connection.wait_replies = [bytes.fromhex(ready) + stale_result]
        step = PhomemoPageStep(label="page", data=b"x", paginated=True, buffered=path == "buffered")
        job = (ProtocolJob(payload=b"x", wait_for_completion=True) if path == "payload" else
               ProtocolJob(steps=(step,), wait_for_completion=True))
        with pytest.raises(RuntimeError, match="completion timed out"):
            await send_prepared_job(context, connection, job)
        assert connection.sent == [b"x"]
        assert connection.wait_replies == []

    asyncio.run(run())
