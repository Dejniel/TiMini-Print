import asyncio

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import ProtocolReplyError, reply_matches_for
from timiniprint.protocol import PaperMode, PrinterProtocol, ProtocolJob, ProtocolStepOperation
from timiniprint.protocol.status import PrinterStatusCode
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def page(device, mode=None):
    raster = RasterSet.from_single(RasterBuffer([1] * 8, 8, PixelFormat.BW1))
    return PrinterProtocol(device).build_job(raster, is_text=False, paper_mode=mode)


class Connection:
    """Exercise both buffered queries and fragment-based notification queries."""

    def __init__(self, *, notifications=False, overrides=None, replies_available=True):
        self.notifications = notifications
        self.replies_available = replies_available
        self.overrides = overrides or {}
        self.events = []

    def can_query_control_packet(self):
        return self.replies_available and not self.notifications

    def can_send_control_packet_wait_notification(self):
        return self.replies_available and self.notifications

    async def send(self, job):
        pytest.fail("Luck transaction must not fall back to an unchecked byte stream")

    async def send_standard_payload(self, data):
        self.events.append(("send", data))

    def _reply(self, packet, timeout):
        self.events.append(("query", packet, timeout))
        return self.overrides.get(packet, b"\x00" if packet == b"\x10\xff\x40" else b"OK")

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        raw = self._reply(packet, timeout)
        if raw:
            reply_complete(raw)
        return raw

    async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
        self.events.append(("arm", label))
        raw = self._reply(packet, timeout)
        if raw:
            for byte in raw:
                if match(bytes([byte])):
                    return bytes([byte])
        return None


def send(device, job, connection):
    prepared = PreparedPrinter(device, runtime_controller_for_device(device))
    asyncio.run(send_prepared_job(prepared, connection, job, timeout=0.25))


@pytest.mark.parametrize("key", ["luck_a2", "luck_qirui_q1", "luck_ppa2l", "luck_a40",
                                 "luck_lujiang_a4", "luck_a49h", "luck_a42_luckp"])
@pytest.mark.parametrize("notifications", [False, True])
def test_each_job_executes_its_full_transaction(catalog, key, notifications):
    device = catalog.device_from_profile(key)
    job = page(device)
    assert job.steps == page(device).steps
    connection = Connection(notifications=notifications)
    send(device, job, connection)
    expected = []
    for step in job.steps:
        if step.operation is ProtocolStepOperation.SEND:
            expected.append(("send", step.data))
        else:
            if notifications:
                expected.append(("arm", step.label))
            expected.append(("query", step.data, step.timeout_sec))
    assert connection.events == expected
    assert job.steps[-1].reply_required
    assert b"\x10\xff\x40" not in job.payload


def test_every_catalog_recipe_has_its_completion_policy(catalog):
    for profile in catalog.profiles:
        family = profile.protocol_default.type.value
        if family not in {"luck_normal", "luck_normal_a4", "aiyin_normal"}:
            continue
        device = catalog.device_from_profile(profile.profile_key)
        for mode in {p.paper_mode for p in profile.paper_presets}:
            job = page(device, mode)
            final = job.steps[-1]
            expected = 60 if family == "aiyin_normal" else (
                120 if device.protocol_variant in {"lujiang_a4", "itp05n", "itp06n"} else 70
            )
            assert final.label == "finalize"
            assert final.timeout_sec == expected, profile.profile_key
            assert final.reply_required
            assert runtime_controller_for_device(device) is not None
            for step in job.steps[:-1]:
                if step.operation is ProtocolStepOperation.QUERY:
                    assert step.timeout_sec == 3
                    assert step.reply_required == (step.label != "paper type")


@pytest.mark.parametrize("status,reason", [(1, PrinterStatusCode.BUSY), (2, PrinterStatusCode.COVER_OPEN),
    (4, PrinterStatusCode.PAPER_OUT), (8, PrinterStatusCode.LOW_BATTERY),
    (16, PrinterStatusCode.OVERHEATED), (64, PrinterStatusCode.OVERHEATED),
    (0xA4, PrinterStatusCode.PAPER_OUT)])
@pytest.mark.parametrize("notifications", [False, True])
def test_status_fault_stops_before_bitmap_with_a_typed_error(catalog, status, reason, notifications):
    device = catalog.device_from_profile("luck_a40")
    conn = Connection(notifications=notifications, overrides={b"\x10\xff\x40": bytes([status])})
    with pytest.raises(PrinterNotReadyError) as error:
        send(device, page(device), conn)
    assert reason in error.value.reasons
    assert all(event[0] != "send" for event in conn.events)


@pytest.mark.parametrize("status", [b"\x00", b"\x20", b"\x80", b"\xa0"])
def test_nonblocking_status_bits_do_not_prevent_print(catalog, status):
    device = catalog.device_from_profile("luck_a2")
    conn = Connection(overrides={b"\x10\xff\x40": status})
    send(device, page(device), conn)
    assert conn.events[-1] == ("query", b"\x10\xff\xf1\x45", 70)


@pytest.mark.parametrize("key,packet", [("luck_a40", b"\x10\xff\x40"),
    ("luck_a2", b"\x10\xff\x10\x00\x01")])
@pytest.mark.parametrize("reply", [None, b""])
def test_missing_required_setup_reply_stops_before_raster(catalog, key, packet, reply):
    device = catalog.device_from_profile(key)
    conn = Connection(overrides={packet: reply})
    with pytest.raises(ProtocolReplyError):
        send(device, page(device), conn)
    assert all(event[0] != "send" for event in conn.events)


@pytest.mark.parametrize("reply", [b"OKextra", b"OK\x00", b"\x00OK", b"NO"])
def test_density_requires_exact_ok(catalog, reply):
    device = catalog.device_from_profile("luck_a2")
    conn = Connection(overrides={b"\x10\xff\x10\x00\x01": reply})
    with pytest.raises(ProtocolReplyError):
        send(device, page(device), conn)


@pytest.mark.parametrize("notifications", [False, True])
@pytest.mark.parametrize("reply", [None, b"NO", b"OKextra"])
def test_paper_setting_waits_but_ignored_failure_does_not_abort(catalog, reply, notifications):
    device = catalog.device_from_profile("luck_lujiang_a4")
    conn = Connection(notifications=notifications, overrides={b"\x1f\x80\x01\x10": reply})
    send(device, page(device), conn)
    assert ("query", b"\x1f\x80\x01\x10", 3) in conn.events
    assert conn.events[-1] == ("query", b"\x10\xff\xf1\x45", 120)


@pytest.mark.parametrize("reply", [None, b"", b"NO", b"\x00\xaa", b"\x00OK"])
@pytest.mark.parametrize("notifications", [False, True])
def test_failed_finalization_prevents_next_page_and_does_not_resend(catalog, reply, notifications):
    device = catalog.device_from_profile("luck_a40")
    single = page(device)
    job = ProtocolJob(steps=single.steps + single.steps)
    conn = Connection(notifications=notifications, overrides={b"\x10\xff\xf1\x45": reply})
    with pytest.raises(ProtocolReplyError) as error:
        send(device, job, conn)
    assert error.value.step.label == "finalize"
    image = next(step.data for step in single.steps if step.label == "bitmap")
    assert conn.events.count(("send", image)) == 1


@pytest.mark.parametrize("reply", [b"OK", b"OKextra", b"\xaa", b"\xaa\x00"])
def test_finalizer_accepts_only_its_documented_prefixes(catalog, reply):
    device = catalog.device_from_profile("luck_a40")
    job = page(device)
    assert reply_matches_for(job.steps[-1], reply)
    send(device, job, Connection(overrides={b"\x10\xff\xf1\x45": reply}))


def test_missing_reply_transport_never_sends_the_raw_payload(catalog):
    device = catalog.device_from_profile("luck_a2")
    conn = Connection(replies_available=False)
    with pytest.raises(RuntimeError, match="requires protocol replies"):
        send(device, page(device, PaperMode.TAG), conn)
    assert conn.events == []
