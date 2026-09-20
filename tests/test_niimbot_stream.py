import asyncio

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.niimbot import NiimbotRuntimeController
from timiniprint.printing.step_execution import ProtocolReplyError
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.families.niimbot.core import (
    NiimbotReplyDecoder, NiimbotResponse, frame, model_id_from_reply,
    parse_packets, response_matcher,
)
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.mark.parametrize("chunk", range(1, 25))
def test_stream_retains_fragments_and_multiple_frames(chunk):
    valid = frame(0xE0, b"\0\x03") + frame(0xF4)
    bad = bytearray(frame(0xB3, bytes(7)))
    bad[-3] ^= 1
    stream = b"noise" + bytes(bad) + valid
    decoder = NiimbotReplyDecoder()
    packets = []
    for offset in range(0, len(stream), chunk):
        packets.extend(decoder.feed(stream[offset:offset + chunk]))
    assert tuple(packets) == parse_packets(valid)


def test_matcher_keeps_complete_reply_before_partial_next_frame():
    matcher = response_matcher(NiimbotResponse.PRINT_STATUS, data_matches=lambda p: p[:2] == b"\0\x03")
    assert matcher.matches(frame(0xB3, b"\0\x01") + frame(0xB3, b"\0\x03") + b"\x55\x55\xf4")


def test_model_code_is_unsigned_and_one_byte_is_high_byte():
    assert model_id_from_reply(frame(0x48, b"\x01")) == 256
    assert model_id_from_reply(frame(0x48, b"\xff\x01")) == 65281
    assert model_id_from_reply(frame(0x48, b"\x01\x02\x03")) is None


class EarlyReplySession:
    def __init__(self, controller, *, observe=True):
        self.controller, self.observe = controller, observe
        self.page_report = True
        self.waits = 0
        self.reject = None

    def can_send_standard_payload(self): return True
    def can_query_control_packet(self): return True
    def report_debug(self, message): pass
    async def send_standard_payload(self, data): pass

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        command = parse_packets(packet)[0].command
        response = {0x21: 0x31, 0x23: 0x33, 1: 2, 3: 4, 0x20: 0x30,
                    0x13: 0x14, 0x15: 0x16, 0xE3: 0xE4, 0xF3: 0xF4}[command]
        raw = frame(response, b"\0" if command == self.reject else b"\x01")
        if command == 0xE3 and self.page_report:
            raw += frame(0xE0, b"\0\x01")
        if self.observe:
            for offset in range(0, len(raw), 2):
                self.controller.handle_notification(self, raw[offset:offset + 2])
        return raw

    async def wait_for_reply(self, label, match, *, timeout, required):
        self.waits += 1
        return None


@pytest.mark.parametrize("observe", [False, True])
def test_early_page_index_is_retained_and_never_reused_by_next_job(observe):
    controller = NiimbotRuntimeController()
    session = EarlyReplySession(controller, observe=observe)
    job = PrinterProtocol(PrinterCatalog.load().device_from_profile("niimbot_d11")).build_job(
        RasterSet.from_single(RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)),
        is_text=False,
    )
    async def send():
        async with controller.job_scope(session, job, timeout=0.01):
            await controller.send_protocol_steps(session, job.steps, timeout=0.01)
    asyncio.run(send())
    assert session.waits == 0
    session.page_report = False
    with pytest.raises(ProtocolReplyError, match="page index"):
        asyncio.run(send())
    assert session.waits == 1


def test_negative_setup_reply_stops_printing():
    controller = NiimbotRuntimeController()
    session = EarlyReplySession(controller)
    session.reject = 0x21
    job = PrinterProtocol(PrinterCatalog.load().device_from_profile("niimbot_d11")).build_job(
        RasterSet.from_single(RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)),
        is_text=False,
    )
    async def send():
        async with controller.job_scope(session, job, timeout=0.01):
            await controller.send_protocol_steps(session, job.steps, timeout=0.01)
    with pytest.raises(ProtocolReplyError, match="set density"):
        asyncio.run(send())


@pytest.mark.parametrize("delivery", ["fragments", "return"])
def test_completion_without_an_observer_accepts_waited_replies(delivery):
    controller = NiimbotRuntimeController()
    session = EarlyReplySession(controller, observe=False)
    session.page_report = False
    job = PrinterProtocol(PrinterCatalog.load().device_from_profile("niimbot_d11")).build_job(
        RasterSet.from_single(RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)),
        is_text=False,
    )
    async def wait(label, match, *, timeout, required):
        raw = frame(0xE0, b"\0\x01")
        if delivery == "fragments":
            for byte in raw:
                matched = match(bytes([byte]))
            assert matched
            return raw[-1:]
        return raw
    session.wait_for_reply = wait
    async def send():
        async with controller.job_scope(session, job, timeout=0.01):
            await controller.send_protocol_steps(session, job.steps, timeout=0.01)
    asyncio.run(send())


@pytest.mark.parametrize("observe", [False, True])
@pytest.mark.parametrize("fault", [frame(0xDB, b"\x02"), frame(0xB3, bytes(6) + b"\x02")])
def test_reference_completion_ack_does_not_hide_paper_out(observe, fault):
    from timiniprint.printing.errors import PrinterNotReadyError
    from timiniprint.protocol.status import PrinterStatusCode

    controller = NiimbotRuntimeController()
    session = EarlyReplySession(controller, observe=observe)
    query = session.query_control_packet
    async def faulty_query(packet, *, timeout, reply_complete):
        raw = await query(packet, timeout=timeout, reply_complete=reply_complete)
        if parse_packets(packet)[0].command == 0xE3:
            if observe:
                controller.handle_notification(session, fault)
            raw += fault
        return raw
    session.query_control_packet = faulty_query
    job = PrinterProtocol(PrinterCatalog.load().device_from_profile("niimbot_d11")).build_job(
        RasterSet.from_single(RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)),
        is_text=False,
    )
    async def send():
        async with controller.job_scope(session, job, timeout=0.01):
            await controller.send_protocol_steps(session, job.steps, timeout=0.01)
    with pytest.raises(PrinterNotReadyError) as exc:
        asyncio.run(send())
    assert exc.value.reasons == (PrinterStatusCode.PAPER_OUT,)


def test_post_end_heartbeat_is_outside_completed_print_scope():
    from timiniprint.protocol import ProtocolJob, ProtocolStep

    controller = NiimbotRuntimeController()
    session = EarlyReplySession(controller)
    base = PrinterProtocol(PrinterCatalog.load().device_from_profile("niimbot_d11")).build_job(
        RasterSet.from_single(RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)),
        is_text=False,
    )
    job = ProtocolJob(steps=base.steps + (ProtocolStep.send("heartbeat", frame(0xDC)),))
    async def write(data):
        if data == frame(0xDC):
            controller.handle_notification(session, frame(0xDB, b"\x01"))
    session.send_standard_payload = write
    async def send():
        async with controller.job_scope(session, job, timeout=0.01):
            await controller.send_protocol_steps(session, job.steps, timeout=0.01)
    asyncio.run(send())
