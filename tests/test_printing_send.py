from __future__ import annotations

from collections.abc import Callable
import unittest
from unittest.mock import patch

from timiniprint.printing.runtime.base import PreparedRuntimeContext, RuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep


class _Reporter:
    def __init__(self) -> None:
        self.debugs: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def debug(self, *, short=None, detail=None, **_kwargs) -> None:
        self.debugs.append((short or "", detail or ""))

    def warning(self, *, short=None, detail=None, **_kwargs) -> None:
        self.warnings.append((short or "", detail or ""))


class _Connection:
    def __init__(
        self,
        *,
        can_query: bool = True,
        replies: list[bytes | None] | None = None,
    ) -> None:
        self.can_query = can_query
        self.replies = list(replies or [])
        self.query_packets: list[bytes] = []
        self.query_timeouts: list[float] = []
        self.query_matchers: list[bool] = []
        self.query_match_results: list[bool] = []
        self.standard_payloads: list[bytes] = []
        self.sent_jobs: list[ProtocolJob] = []
        self.notification_query_packets: list[bytes] = []
        self.attached_runtime_controllers: list[RuntimeController] = []

    async def attach_runtime_controller(
        self,
        runtime_controller: RuntimeController,
        *,
        timeout: float = 1.0,
    ) -> None:
        _ = timeout
        self.attached_runtime_controllers.append(runtime_controller)

    def can_query_control_packet(self) -> bool:
        return self.can_query

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        self.query_packets.append(bytes(packet))
        self.query_timeouts.append(timeout)
        self.query_matchers.append(reply_complete is not None)
        if self.replies:
            reply = self.replies.pop(0)
            if reply is not None and reply_complete is not None:
                self.query_match_results.append(reply_complete(reply))
            return reply
        return None

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        return None

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))


class _NotificationConnection(_Connection):
    def __init__(self, *, replies: list[bytes | None]) -> None:
        super().__init__(can_query=False, replies=replies)

    def can_wait_for_notification(self) -> bool:
        return True

    def can_send_control_packet_wait_notification(self) -> bool:
        return True

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        _ = label, timeout, required
        self.notification_query_packets.append(bytes(packet))
        if not self.replies:
            return None
        reply = self.replies.pop(0)
        if reply is not None:
            self.query_match_results.append(match(reply))
        return reply


class _WaitOnlyConnection(_Connection):
    def __init__(self) -> None:
        super().__init__(can_query=False)
        self.wait_labels: list[str] = []
        self.wait_timeouts: list[float] = []
        self.wait_match_results: list[bool] = []

    def can_wait_for_notification(self) -> bool:
        return True

    async def wait_for_notification(
        self,
        label: str,
        match,
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        _ = required
        self.wait_labels.append(label)
        self.wait_timeouts.append(timeout)
        self.wait_match_results.append(match(b"ACK"))
        return b"ACK"


class _SendOnlyConnection:
    def __init__(self) -> None:
        self.sent_jobs: list[ProtocolJob] = []

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        return None


class _RuntimeStepController(RuntimeController):
    def __init__(self) -> None:
        self.calls = 0

    async def send_protocol_steps(self, session, steps, *, timeout: float) -> bool:
        _ = steps, timeout
        self.calls += 1
        await session.send_standard_payload(b"runtime")
        return True


class PrintingSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_job_can_be_step_only(self) -> None:
        steps = (
            ProtocolStep.send("setup", b"A"),
            ProtocolStep.query(
                "status",
                b"S",
                expect=ProtocolReplyExpectation.STATUS_ZERO,
                include_in_payload=False,
            ),
            ProtocolStep.send("bitmap", b"B"),
        )
        job = ProtocolJob(steps=steps)

        self.assertEqual(job.steps, steps)
        self.assertEqual(job.payload_segments, (b"A", b"B"))
        self.assertEqual(job.payload, b"AB")

    async def test_send_prepared_job_executes_protocol_steps(self) -> None:
        connection = _Connection(replies=[b"\x00OK", b"\x00", b"\x00\xAA"])
        reporter = _Reporter()
        steps = (
            ProtocolStep.query("density", b"D", expect=ProtocolReplyExpectation.OK, timeout_sec=0.25),
            ProtocolStep.query(
                "status",
                b"S",
                expect=ProtocolReplyExpectation.STATUS_ZERO,
                timeout_sec=0.25,
                include_in_payload=False,
            ),
            ProtocolStep.send("bitmap", b"B"),
            ProtocolStep.query("finalize", b"F", expect=ProtocolReplyExpectation.OK_OR_AA, timeout_sec=0.25),
        )
        job = ProtocolJob(steps=steps)

        await send_prepared_job(object(), connection, job, timeout=0.1, reporter=reporter)

        self.assertEqual(connection.query_packets, [b"D", b"S", b"F"])
        self.assertEqual(connection.query_timeouts, [0.25, 0.25, 0.25])
        self.assertEqual(connection.query_matchers, [True, True, True])
        self.assertEqual(connection.query_match_results, [True, True, True])
        self.assertEqual(connection.standard_payloads, [b"B"])
        self.assertEqual(connection.sent_jobs, [])
        self.assertTrue(any("Protocol query finalize" in detail for _short, detail in reporter.debugs))
        self.assertEqual(reporter.warnings, [])

    async def test_send_prepared_job_allows_runtime_controller_to_execute_steps(self) -> None:
        connection = _Connection()
        controller = _RuntimeStepController()
        job = ProtocolJob(
            steps=(ProtocolStep.send("bitmap", b"B"),),
        )

        await send_prepared_job(
            object(),
            connection,
            job,
            timeout=0.1,
            runtime_context=PreparedRuntimeContext(runtime_controller=controller),
        )

        self.assertEqual(controller.calls, 1)
        self.assertEqual(connection.attached_runtime_controllers, [controller])
        self.assertEqual(connection.standard_payloads, [b"runtime"])
        self.assertEqual(connection.sent_jobs, [])

    async def test_send_prepared_job_attaches_runtime_controller_for_stream_job(self) -> None:
        connection = _Connection()
        controller = RuntimeController()
        job = ProtocolJob(payload=b"stream")

        await send_prepared_job(
            object(),
            connection,
            job,
            timeout=0.1,
            runtime_context=PreparedRuntimeContext(runtime_controller=controller),
        )

        self.assertEqual(connection.attached_runtime_controllers, [controller])
        self.assertEqual(connection.sent_jobs, [job])

    async def test_send_prepared_job_resolves_runtime_for_print_job(self) -> None:
        connection = _Connection()
        controller = RuntimeController()
        device = object()
        job = ProtocolJob(payload=b"print", wait_for_completion=True)

        with patch(
            "timiniprint.printing.send.runtime_controller_for_device",
            return_value=controller,
        ) as runtime_factory:
            await send_prepared_job(device, connection, job, timeout=0.1)

        runtime_factory.assert_called_once_with(device)
        self.assertEqual(connection.attached_runtime_controllers, [controller])
        self.assertEqual(connection.sent_jobs, [job])

    async def test_send_prepared_job_executes_wait_steps(self) -> None:
        connection = _WaitOnlyConnection()
        reporter = _Reporter()
        matcher = ProtocolReplyMatcher(
            complete=lambda reply: reply == b"ACK",
            matches=lambda reply: reply == b"ACK",
        )
        steps = (
            ProtocolStep.send("setup", b"A"),
            ProtocolStep.wait("page index", reply_matcher=matcher, timeout_sec=0.5),
            ProtocolStep.send("final", b"B"),
        )
        job = ProtocolJob(steps=steps)

        await send_prepared_job(object(), connection, job, timeout=0.1, reporter=reporter)

        self.assertEqual(connection.standard_payloads, [b"A", b"B"])
        self.assertEqual(connection.wait_labels, ["page index"])
        self.assertEqual(connection.wait_timeouts, [0.5])
        self.assertEqual(connection.wait_match_results, [True])
        self.assertTrue(any("Protocol wait page index" in detail for _short, detail in reporter.debugs))
        self.assertEqual(reporter.warnings, [])

    async def test_send_prepared_job_falls_back_when_query_transport_is_unavailable(self) -> None:
        connection = _Connection(can_query=False)
        reporter = _Reporter()
        job = ProtocolJob(
            steps=(
                ProtocolStep.query("density", b"D", expect=ProtocolReplyExpectation.OK),
                ProtocolStep.send("bitmap", b"B"),
            )
        )

        await send_prepared_job(object(), connection, job, reporter=reporter)

        self.assertEqual(connection.query_packets, [])
        self.assertEqual(connection.standard_payloads, [])
        self.assertEqual(len(connection.sent_jobs), 1)
        self.assertEqual(connection.sent_jobs[0].payload, b"DB")
        self.assertEqual(len(reporter.warnings), 1)
        self.assertIn("stream-only", reporter.warnings[0][1])

    async def test_send_prepared_job_falls_back_for_send_only_connections(self) -> None:
        connection = _SendOnlyConnection()
        reporter = _Reporter()
        job = ProtocolJob(steps=(ProtocolStep.send("bitmap", b"B"),))

        await send_prepared_job(object(), connection, job, reporter=reporter)

        self.assertEqual(len(connection.sent_jobs), 1)
        self.assertEqual(connection.sent_jobs[0].payload, b"B")
        self.assertEqual(len(reporter.warnings), 1)
        self.assertIn("raw standard payload chunks", reporter.warnings[0][1])

    async def test_send_prepared_job_can_query_via_ble_notification(self) -> None:
        matcher = ProtocolReplyMatcher(
            complete=lambda reply: reply == b"ACK",
            matches=lambda reply: reply == b"ACK",
        )
        connection = _NotificationConnection(replies=[b"ACK"])
        reporter = _Reporter()
        job = ProtocolJob(
            steps=(
                ProtocolStep.query(
                    "notify query",
                    b"Q",
                    expect=ProtocolReplyExpectation.NONE,
                    reply_matcher=matcher,
                ),
            )
        )

        await send_prepared_job(object(), connection, job, reporter=reporter)

        self.assertEqual(connection.notification_query_packets, [b"Q"])
        self.assertEqual(connection.query_match_results, [True])
        self.assertEqual(connection.sent_jobs, [])
        self.assertEqual(reporter.warnings, [])

    async def test_send_prepared_job_requires_atomic_ble_notification_query(self) -> None:
        matcher = ProtocolReplyMatcher(
            complete=lambda reply: reply == b"ACK",
            matches=lambda reply: reply == b"ACK",
        )
        connection = _WaitOnlyConnection()
        reporter = _Reporter()
        job = ProtocolJob(
            steps=(
                ProtocolStep.query(
                    "notify query",
                    b"Q",
                    expect=ProtocolReplyExpectation.NONE,
                    reply_matcher=matcher,
                ),
            )
        )

        await send_prepared_job(object(), connection, job, reporter=reporter)

        self.assertEqual(connection.notification_query_packets, [])
        self.assertEqual(connection.query_match_results, [])
        self.assertEqual(len(connection.sent_jobs), 1)
        self.assertEqual(reporter.warnings[0][0], "Protocol query unavailable")

    async def test_repeated_query_total_timeout_caps_single_attempt_timeout(self) -> None:
        matcher = ProtocolReplyMatcher(
            complete=lambda reply: reply == b"ACK",
            matches=lambda reply: reply == b"ACK",
        )
        connection = _Connection(replies=[None])
        reporter = _Reporter()
        job = ProtocolJob(
            steps=(
                ProtocolStep.query(
                    "poll",
                    b"P",
                    expect=ProtocolReplyExpectation.NONE,
                    timeout_sec=10.0,
                    reply_matcher=matcher,
                    repeat_interval_sec=1.0,
                    repeat_timeout_sec=0.01,
                ),
            )
        )

        await send_prepared_job(object(), connection, job, timeout=5.0, reporter=reporter)

        self.assertEqual(connection.query_packets, [b"P"])
        self.assertLessEqual(connection.query_timeouts[0], 0.01)


if __name__ == "__main__":
    unittest.main()
