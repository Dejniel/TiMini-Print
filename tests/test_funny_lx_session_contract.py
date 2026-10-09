import asyncio

import pytest

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.funny_lx import FunnyLxRuntimeController
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import ProtocolReplyError
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.families.funny_lx.core import challenge_crc, decode_status
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class Connection:
    def __init__(self, *, darkness=False, footer=b"\x5a\x04\x00\x01\x01", replies_available=True):
        self.darkness = darkness
        self.footer = footer
        self.replies_available = replies_available
        self.sent = []
        self.stream_jobs = []
        self.flow_changes = []

    def set_flow_paused(self, paused, *, payload=b""):
        self.flow_changes.append(paused)

    def can_send_control_packet_wait_notification(self):
        return self.replies_available

    def can_wait_for_notification(self):
        return self.replies_available

    def can_wait_for_reply(self):
        return self.replies_available

    async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required=True):
        self.sent.append(packet)
        if packet[:2] == b"\x5a\x01":
            return b"\x5a\x01" + (b"\x00\x03" if self.darkness else b"\x00\x00") + bytes.fromhex("c00000000460")
        if packet[:2] == b"\x5a\x0a":
            return b"\x5a\x0a" + challenge_crc(packet[2:], bytes.fromhex("c00000000460")).low
        if packet[:2] == b"\x5a\x0b":
            return b"\x5a\x0b\x01"
        return self.footer

    async def send_control_packet(self, packet, *, timeout):
        self.sent.append(packet)
        return True

    async def send_standard_payload(self, payload):
        self.sent.append(payload)

    async def wait_for_notification(self, label, match, *, timeout, required=True):
        return b"\x5a\x06\x00"

    async def wait_for_reply(self, label, match, *, timeout, required=True):
        return await self.wait_for_notification(label, match, timeout=timeout, required=required)

    async def send(self, job):
        self.stream_jobs.append(job)


@pytest.fixture(scope="module")
def device():
    return PrinterCatalog.load().device_from_model("funny_lx_d")


def build_job(device, capabilities=None):
    raster = RasterSet.from_single(RasterBuffer([1] + [0] * 767, 384, PixelFormat.BW1))
    return PrinterProtocol(device).build_job(raster, is_text=False, blackening=5,
                                           runtime_capabilities=capabilities)


async def prepare(device, connection):
    controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")
    session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
    await controller.initialize_connection(session, mtu_size=508, timeout=0.1)
    return await controller.prepare(device, session, timeout=0.1)


@pytest.mark.parametrize("darkness", [False, True])
@pytest.mark.parametrize("prepared_job", [False, True])
def test_negotiated_darkness_controls_new_and_prebuilt_jobs(device, darkness, prepared_job):
    connection = Connection(darkness=darkness)
    async def run():
        prepared = await prepare(device, connection)
        job = build_job(device, prepared.capabilities if prepared_job else None)
        if prepared_job:
            assert any(step.label == "darkness" for step in job.steps) is darkness
        await send_prepared_job(prepared, connection, job)
    asyncio.run(run())
    assert (b"\x5a\x0c\x04" in connection.sent) is darkness
    assert any(packet.startswith(b"\x55") for packet in connection.sent)


@pytest.mark.parametrize("footer", [None, b"\x5a\x04", b"\x5a\x04\x00\x02\x01", b"\x5a\x04\x00\x01\x00"])
def test_missing_or_invalid_footer_is_not_reported_as_success(device, footer):
    connection = Connection(footer=footer)
    async def run():
        prepared = await prepare(device, connection)
        with pytest.raises(ProtocolReplyError) as caught:
            await send_prepared_job(prepared, connection, build_job(device))
        assert caught.value.step.label == "print footer"
    asyncio.run(run())


def test_missing_reply_operations_do_not_fall_back_to_blind_stream(device):
    connection = Connection(replies_available=False)
    controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")
    async def run():
        with pytest.raises(RuntimeError, match="requires protocol replies"):
            await send_prepared_job(PreparedPrinter(device, controller), connection, build_job(device))
    asyncio.run(run())
    assert connection.sent == []
    assert connection.stream_jobs == []


def test_passive_status_is_diagnostic_not_a_flow_or_completion_reply():
    controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")
    connection = Connection()
    session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
    reply = bytes.fromhex("5a 02 57 04 99 02 03 03")
    controller.handle_notification(session, reply)
    assert controller.debug_snapshot()["status"] == {
        "battery": 87, "paper_status": 4, "temperature_status": 2,
        "voltage_status": 3, "darkness_level": 4,
    }
    assert connection.flow_changes == []
    assert controller.debug_snapshot()["verified"] is False
    assert connection.sent == []
    for partial in (b"", reply[:2], reply[:7], b"\x5a\x01" + reply[2:]):
        assert decode_status(partial) is None
        controller.handle_notification(session, partial)
    assert controller.debug_snapshot()["status"]["battery"] == 87
