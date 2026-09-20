from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from tests.runtime_helpers import prepare_and_send
from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import LevelProfile, ModeLevelProfile, SpeedProfile
from timiniprint.printing import PrinterNotReadyError
from timiniprint.protocol import PrinterProtocol, PrinterStatusCode, ProtocolJob, ProtocolStep
from timiniprint.protocol.families.phomemo_esc.replies import PrintMasterReplyDecoder
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet

# These payloads intentionally contain complete-looking completion/fault bytes.
_REPLIES = (
    bytes.fromhex("03a8"), bytes.fromhex("040f"), bytes.fromhex("0598"), bytes.fromhex("0600"),
    bytes.fromhex("071a0f0c"), b"\x08" + b"Q194" + bytes.fromhex("1a0f0c1b05881a06990000"),
    bytes.fromhex("0bb8"), bytes.fromhex("0c26"), bytes.fromhex("0f0c"),
    bytes.fromhex("15020f0c"), bytes.fromhex("1703"), bytes.fromhex("31021a0f"),
    bytes.fromhex("3b1a0f0c"), bytes.fromhex("3e00"), bytes.fromhex("3f02"),
    bytes.fromhex("4012340200070903021a0f0c00301e"),
)


@pytest.mark.parametrize("prefix", [b"", b"\x1a", b"\x1b"])
def test_all_reply_lengths_preserve_boundaries_and_arbitrary_fragments(prefix):
    stream = b"".join(prefix + frame for frame in _REPLIES)
    for split in range(len(stream) + 1):
        decoder = PrintMasterReplyDecoder()
        assert decoder.feed(stream[:split]) + decoder.feed(stream[split:]) == list(_REPLIES)
    decoder = PrintMasterReplyDecoder()
    assert [frame for value in stream for frame in decoder.feed(bytes([value]))] == list(_REPLIES)


class _ObservedConnection:
    def __init__(self, frames):
        self.frames = frames
        self.controller = None
        self.waits = []
        self.sent = []

    async def attach_runtime_controller(self, controller, *, timeout):
        self.controller = controller

    def report_debug(self, message):
        pass

    def can_wait_for_reply(self):
        return True

    async def send(self, job):
        await self.send_standard_payload(job.payload)

    async def send_standard_payload(self, data):
        self.sent.append(data)
        # Deliver statuses during sending, before the completion wait starts.
        for part in self.frames:
            self.controller.handle_notification(self, part)

    async def wait_for_reply(self, label, match, *, timeout, required):
        self.waits.append(label)
        assert not match(b"\x1a\x0f\x0c")  # Historical bytes are not current job state.
        return None


@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("frames,reason", [
    ([b"\x1b\x0f", b"\x0c"], None),
    ([b"\x03", b"\xa9"], PrinterStatusCode.OVERHEATED),
    ([b"\x1b\x05\x99"], PrinterStatusCode.COVER_OPEN),
    ([b"\x06\x88"], PrinterStatusCode.PAPER_OUT),
    ([b"\x0b\xb8"], PrinterStatusCode.NOT_READY),
])
def test_early_status_applies_to_both_send_paths(steps, frames, reason):
    device = PrinterCatalog.load().device_from_model("printmaster_m110")
    connection = _ObservedConnection(frames)
    job = ProtocolJob(payload=b"raster", wait_for_completion=True,
                      steps=(ProtocolStep.send("raster", b"raster"),) if steps else ())

    async def run():
        if reason:
            with pytest.raises(PrinterNotReadyError) as caught:
                await prepare_and_send(device, connection, job)
            assert caught.value.reasons == (reason,)
        else:
            await prepare_and_send(device, connection, job)
        assert connection.sent == [b"raster"]
        assert connection.waits == []  # The early result was retained.
    asyncio.run(run())


@pytest.mark.parametrize("frames", [
    [bytes.fromhex("1a071a0f0c")],  # Firmware is one reply, not an embedded completion.
    [bytes.fromhex("1b071a0f0c")],
    [bytes.fromhex("07030599")],  # Nor an embedded cover fault.
    [b"\x0f\x00"], [b"\x0f\x01"], [b"\x3e\x00"],  # Unknown result / idle.
    [b"\x03\x00", b"\x05\x00", b"\x06\x89"],  # Not faults.
])
def test_auxiliary_and_unknown_statuses_do_not_finish_print(frames):
    async def run():
        connection = _ObservedConnection(frames)
        with pytest.raises(RuntimeError, match="completion timed out"):
            await prepare_and_send(PrinterCatalog.load().device_from_model("printmaster_m110"),
                                   connection, ProtocolJob(payload=b"raster", wait_for_completion=True),
                                   timeout=0.01)
        assert connection.waits == ["Print Master completion"]
    asyncio.run(run())


@pytest.mark.parametrize("key", ["printmaster_m110", "printmaster_m120"])
def test_explicit_density_and_speed_are_sent_before_reset(key):
    device = PrinterCatalog.load().device_from_model(key)
    levels = LevelProfile(low=1, middle=3, high=5)
    defaults = replace(device.profile.print_defaults,
                       density=ModeLevelProfile(image=levels, text=levels),
                       speed=SpeedProfile(image=2, text=2))
    device = replace(device, profile=replace(device.profile, print_defaults=defaults))
    raster = RasterSet.from_single(RasterBuffer([0] * 384, 384, PixelFormat.BW1))
    job = PrinterProtocol(device).build_job(raster, is_text=False, blackening=3)
    setup, _, _ = job.payload.partition(b"\x1b\x40")
    assert bytes.fromhex("1f1102031f112302") in setup
