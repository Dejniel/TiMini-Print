from timiniprint.printing.runtime.base import PreparedPrinter
import asyncio

import pytest

from tests.test_printing_send import _Connection, _Reporter
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import execute_protocol_step
from timiniprint.protocol import (
    ProtocolFamily, ProtocolJob, ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep,
)


class _GenericDevice:
    protocol_family = ProtocolFamily.TINY
    protocol_variant = None


class FragmentedConnection(_Connection):
    def __init__(self, responses, *, notification_only=True):
        super().__init__(can_query=not notification_only)
        self.responses = iter(responses)

    def can_send_control_packet_wait_notification(self):
        return not self.can_query

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        self.query_packets.append(packet)
        buffer = bytearray()
        for fragment in next(self.responses):
            buffer.extend(fragment)
            if reply_complete(bytes(buffer)):
                break
        return bytes(buffer) or None

    async def send_control_packet_wait_notification(self, packet, *, match, **kwargs):
        self.notification_query_packets.append(packet)
        for fragment in next(self.responses):
            if match(fragment):
                return fragment
        return None


def query_step(*, expected=b"ACK", **kwargs):
    return ProtocolStep.query(
        "stream reply", b"Q", expect=ProtocolReplyExpectation.NONE,
        reply_matcher=ProtocolReplyMatcher(
            complete=lambda data: data.endswith(b"ACK"),
            matches=lambda data: data == expected,
        ),
        reply_required=True, **kwargs,
    )


@pytest.mark.parametrize("notification_only", [False, True])
@pytest.mark.parametrize("fragments", [(b"ACK",), (b"A", b"C", b"K"), (b"WAIT", b"ACK")])
def test_queries_return_the_complete_response_on_both_paths(notification_only, fragments):
    connection = FragmentedConnection([fragments], notification_only=notification_only)
    expected = b"".join(fragments)
    session = RuntimeConnectionSession(connection, reporter=_Reporter())
    result = asyncio.run(execute_protocol_step(session, query_step(expected=expected), timeout=0.1))
    assert result == expected


def test_reusing_a_query_step_does_not_reuse_its_reply_buffer():
    connection = FragmentedConnection([(b"A", b"CK"), (b"AC", b"K")])
    step = query_step()
    job = ProtocolJob(steps=(step, ProtocolStep.send("raster", b"DATA"), step))
    asyncio.run(send_prepared_job(PreparedPrinter(_GenericDevice()), connection, job))
    assert connection.notification_query_packets == [b"Q", b"Q"]
    assert connection.standard_payloads == [b"DATA"]
    assert not connection.sent_jobs


def test_repeated_query_discards_partial_reply_before_the_next_attempt():
    connection = FragmentedConnection([(b"A",), (b"AC", b"K")])
    step = query_step(repeat_interval_sec=0.001, repeat_timeout_sec=1.0)
    session = RuntimeConnectionSession(connection, reporter=_Reporter())
    assert asyncio.run(execute_protocol_step(session, step, timeout=0.1)) == b"ACK"
    assert connection.notification_query_packets == [b"Q", b"Q"]


@pytest.mark.parametrize("fragments", [(), (b"A", b"C"), (b"N", b"ACK")])
def test_incomplete_or_negative_response_stops_before_raster_and_keeps_reply(fragments):
    connection = FragmentedConnection([fragments])
    step = query_step()
    job = ProtocolJob(steps=(step, ProtocolStep.send("raster", b"DATA")))
    with pytest.raises(RuntimeError, match="Required protocol reply") as raised:
        asyncio.run(send_prepared_job(PreparedPrinter(_GenericDevice()), connection, job))
    assert f"got {b''.join(fragments).hex(' ') if fragments else '<none>'}" in str(raised.value)
    assert not connection.standard_payloads
    assert not connection.sent_jobs


def test_notification_query_limits_unmatched_reply_data():
    connection = FragmentedConnection([(b"X" * 4096,) * 17])
    job = ProtocolJob(steps=(query_step(), ProtocolStep.send("raster", b"DATA")))
    with pytest.raises(RuntimeError, match="reassembly limit"):
        asyncio.run(send_prepared_job(PreparedPrinter(_GenericDevice()), connection, job))
    assert not connection.standard_payloads
    assert not connection.sent_jobs
