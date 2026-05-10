from __future__ import annotations

import unittest

from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolStep


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
        self.standard_payloads: list[bytes] = []
        self.sent_jobs: list[ProtocolJob] = []

    def can_query_control_packet(self) -> bool:
        return self.can_query

    async def query_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bytes | None:
        _ = timeout
        self.query_packets.append(bytes(packet))
        if self.replies:
            return self.replies.pop(0)
        return None

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        return None

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))


class _SendOnlyConnection:
    def __init__(self) -> None:
        self.sent_jobs: list[ProtocolJob] = []

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        return None


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
        connection = _Connection(replies=[b"OK", b"\x00", b"\xAA"])
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

        await send_prepared_job(object(), connection, job, reporter=reporter)

        self.assertEqual(connection.query_packets, [b"D", b"S", b"F"])
        self.assertEqual(connection.standard_payloads, [b"B"])
        self.assertEqual(connection.sent_jobs, [])
        self.assertTrue(any("Protocol query finalize" in detail for _short, detail in reporter.debugs))
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


if __name__ == "__main__":
    unittest.main()
