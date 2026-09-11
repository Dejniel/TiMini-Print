from __future__ import annotations

import unittest
from dataclasses import replace

from tests.test_printing_send import (
    _Connection, _NotificationConnection, _Reporter,
    _SendOnlyConnection, _WaitOnlyConnection,
)
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import (
    PrinterProtocol, ProtocolFamily, ProtocolJob, ProtocolReplyExpectation,
    ProtocolReplyMatcher, ProtocolStep,
)
from timiniprint.protocol.families.niimbot.core import (
    NiimbotRequest, NiimbotResponse, frame,
)
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class _GenericDevice:
    protocol_family = ProtocolFamily.TINY


class _NiimbotCompletionConnection(_Connection):
    def __init__(self, *, done, notification_only):
        super().__init__(can_query=not notification_only)
        self.done = done
        self.notification_only = notification_only

    def can_send_control_packet_wait_notification(self):
        return self.notification_only

    def can_wait_for_reply(self):
        return True

    def _response(self, packet):
        request = NiimbotRequest(packet[2])
        if request is NiimbotRequest.PRINT_STATUS:
            data = b"\x00\x01" if self.done else b"\x00\x00"
        elif request is NiimbotRequest.PRINT_END:
            data = b"\x01" if self.done else b"\x00"
        else:
            data = b"\x01"
        return frame(NiimbotResponse[request.name], data)

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        self.query_packets.append(packet)
        return self._response(packet)

    async def send_control_packet_wait_notification(self, packet, **kwargs):
        self.notification_query_packets.append(packet)
        return self._response(packet)

    async def wait_for_reply(self, label, match, *, timeout, required):
        return frame(NiimbotResponse.PRINTER_PAGE_INDEX, b"\x00\x01") if self.done else None


class RequiredProtocolReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_niimbot_recipes_require_completion_on_both_reply_paths(self):
        catalog = PrinterCatalog.load()
        for profile in catalog.profiles:
            device = catalog.device_from_profile(profile.profile_key)
            if device.protocol_family is not ProtocolFamily.NIIMBOT or device.protocol_variant.endswith("_auto"):
                continue
            width = profile.default_paper_preset.paper_width_px
            raster = RasterSet.from_single(RasterBuffer(
                pixels=[0] * width, width=width, pixel_format=PixelFormat.BW1,
            ))
            built = PrinterProtocol(device).build_job(raster, is_text=False)
            completion = [s for s in built.steps if s.label in ("print status", "page index", "print end")]
            self.assertTrue(completion)
            self.assertTrue(all(s.reply_required for s in completion))
            self.assertTrue(all(not s.reply_required for s in built.steps if s not in completion))
            steps = tuple(replace(s, repeat_interval_sec=0.001, repeat_timeout_sec=0.002)
                          if s.repeat_interval_sec else s for s in built.steps)
            for notification_only in (False, True):
                for done in (False, True):
                    with self.subTest(profile=profile.profile_key, ble=notification_only, done=done):
                        connection = _NiimbotCompletionConnection(done=done, notification_only=notification_only)
                        job = ProtocolJob(steps=steps, wait_for_completion=True)
                        if done:
                            await send_prepared_job(device, connection, job)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "Required protocol reply"):
                                await send_prepared_job(device, connection, job)
                        sent = connection.query_packets + connection.notification_query_packets
                        if not done and any(s.label in ("print status", "page index") for s in completion):
                            self.assertNotIn(frame(NiimbotRequest.PRINT_END), sent)
                        self.assertEqual(connection.sent_jobs, [])

    async def test_required_query_failure_stops_before_next_step(self):
        for connection_type in (_Connection, _NotificationConnection):
            for repeat in (False, True):
                for reply in (None, b"busy"):
                    with self.subTest(connection=connection_type, repeat=repeat, reply=reply):
                        connection = connection_type(replies=[reply])
                        step = ProtocolStep.query(
                            "completion", b"Q", expect=ProtocolReplyExpectation.OK,
                            reply_required=True,
                            repeat_interval_sec=0.001 if repeat else None,
                            repeat_timeout_sec=0.002 if repeat else None,
                        )
                        job = ProtocolJob(steps=(step, ProtocolStep.send("end", b"E")))
                        with self.assertRaisesRegex(RuntimeError, "completion"):
                            await send_prepared_job(_GenericDevice(), connection, job)
                        self.assertEqual(connection.standard_payloads, [])
                        self.assertEqual(connection.sent_jobs, [])

    async def test_required_poll_accepts_later_confirmation(self):
        for connection_type in (_Connection, _NotificationConnection):
            connection = connection_type(replies=[b"busy", b"OK"])
            step = ProtocolStep.query(
                "completion", b"Q", expect=ProtocolReplyExpectation.OK,
                reply_required=True, repeat_interval_sec=0.001, repeat_timeout_sec=1,
            )
            await send_prepared_job(
                _GenericDevice(), connection,
                ProtocolJob(steps=(step, ProtocolStep.send("end", b"E"))),
            )
            self.assertEqual(connection.standard_payloads, [b"E"])

    async def test_required_passive_wait_checks_confirmation(self):
        for accepted in (False, True):
            connection = _WaitOnlyConnection()
            step = ProtocolStep.wait(
                "page index",
                reply_matcher=ProtocolReplyMatcher(
                    complete=lambda reply: reply == b"ACK",
                    matches=lambda reply: accepted and reply == b"ACK",
                ),
                reply_required=True,
            )
            job = ProtocolJob(steps=(step, ProtocolStep.send("end", b"E")))
            if accepted:
                await send_prepared_job(_GenericDevice(), connection, job)
                self.assertEqual(connection.standard_payloads, [b"E"])
            else:
                with self.assertRaisesRegex(RuntimeError, "page index"):
                    await send_prepared_job(_GenericDevice(), connection, job)
                self.assertEqual(connection.standard_payloads, [])

    async def test_required_steps_cannot_fall_back_to_unchecked_stream(self):
        for operation in ("query", "wait"):
            for connection in (_Connection(can_query=False), _SendOnlyConnection()):
                with self.subTest(operation=operation, connection=type(connection)):
                    step = (
                        ProtocolStep.query(
                            "completion", b"Q", expect=ProtocolReplyExpectation.OK,
                            reply_required=True,
                        )
                        if operation == "query" else ProtocolStep.wait(
                            "completion",
                            reply_matcher=ProtocolReplyMatcher(lambda reply: reply == b"OK"),
                            reply_required=True,
                        )
                    )
                    with self.assertRaisesRegex(RuntimeError, "stream-only"):
                        await send_prepared_job(
                            _GenericDevice(), connection, ProtocolJob(steps=(step,)),
                        )
                    self.assertEqual(connection.sent_jobs, [])

    async def test_optional_reply_and_raw_payload_keep_existing_behavior(self):
        connection = _Connection(replies=[b"busy"])
        reporter = _Reporter()
        await send_prepared_job(
            _GenericDevice(), connection,
            ProtocolJob(steps=(
                ProtocolStep.query("optional", b"Q", expect=ProtocolReplyExpectation.OK),
                ProtocolStep.send("image", b"I"),
            )),
            reporter=reporter,
        )
        self.assertEqual(connection.standard_payloads, [b"I"])
        self.assertTrue(reporter.warnings)
        raw_job = ProtocolJob(payload=b"raw")
        await send_prepared_job(_GenericDevice(), connection, raw_job)
        self.assertEqual(connection.sent_jobs, [raw_job])

    def test_required_reply_must_have_an_expectation(self):
        with self.assertRaises(ValueError):
            ProtocolStep("send", b"X", reply_required=True)
        with self.assertRaises(ValueError):
            ProtocolStep.query(
                "query", b"X", expect=ProtocolReplyExpectation.NONE, reply_required=True,
            )

    async def test_d110_never_sends_print_end_before_confirmed_page(self):
        catalog = PrinterCatalog.load()
        device = catalog.device_from_profile("niimbot_d110")
        width = device.profile.default_paper_preset.paper_width_px
        raster = RasterSet.from_single(RasterBuffer(
            pixels=[0] * width, width=width, pixel_format=PixelFormat.BW1,
        ))
        built = PrinterProtocol(device).build_job(raster, is_text=False)
        status = next(step for step in built.steps if step.label == "print status")
        finish = next(step for step in built.steps if step.label == "print end")
        self.assertTrue(status.reply_required)
        self.assertTrue(finish.reply_required)
        status = replace(status, repeat_timeout_sec=0.002, repeat_interval_sec=0.001)
        for connection_type in (_Connection, _NotificationConnection):
            connection = connection_type(replies=[
                frame(NiimbotResponse.PRINT_STATUS, b"\x00\x00"),
            ])
            with self.assertRaisesRegex(RuntimeError, "print status"):
                await send_prepared_job(device, connection, ProtocolJob(steps=(status, finish)))
            sent = connection.query_packets + connection.notification_query_packets
            self.assertNotIn(frame(NiimbotRequest.PRINT_END), sent)
