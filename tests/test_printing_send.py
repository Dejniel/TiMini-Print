from __future__ import annotations

import unittest

from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob


class _Controller(RuntimeController):
    def __init__(self, handled: set[bytes]) -> None:
        self.handled = handled
        self.seen: list[bytes] = []

    async def send_standard_job_payload(self, session, data: bytes, *, timeout: float) -> bool:
        _ = timeout
        self.seen.append(bytes(data))
        if data not in self.handled:
            return False
        await session.send_standard_payload(b"handled:" + data)
        return True


class _Connection:
    def __init__(self) -> None:
        self.standard_payloads: list[bytes] = []
        self.sent_jobs: list[ProtocolJob] = []

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        return None

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))


class PrintingSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_job_can_be_segment_only(self) -> None:
        job = ProtocolJob(payload_segments=(b"A", b"B"))

        self.assertEqual(job.payload_segments, (b"A", b"B"))
        self.assertEqual(job.payload, b"AB")

    async def test_send_prepared_job_lets_runtime_handle_all_segments(self) -> None:
        controller = _Controller({b"A", b"B"})
        connection = _Connection()
        job = ProtocolJob(
            payload=b"AB",
            runtime_controller=controller,
            payload_segments=(b"A", b"B"),
        )

        await send_prepared_job(object(), connection, job)

        self.assertEqual(controller.seen, [b"A", b"B"])
        self.assertEqual(connection.standard_payloads, [b"handled:A", b"handled:B"])
        self.assertEqual(connection.sent_jobs, [])

    async def test_send_prepared_job_falls_back_with_remaining_segments(self) -> None:
        controller = _Controller({b"A"})
        connection = _Connection()
        job = ProtocolJob(
            payload=b"ABC",
            runtime_controller=controller,
            payload_segments=(b"A", b"B", b"C"),
        )

        await send_prepared_job(object(), connection, job)

        self.assertEqual(controller.seen, [b"A", b"B"])
        self.assertEqual(connection.standard_payloads, [b"handled:A"])
        self.assertEqual(len(connection.sent_jobs), 1)
        self.assertEqual(connection.sent_jobs[0].payload, b"BC")
        self.assertEqual(connection.sent_jobs[0].payload_segments, (b"B", b"C"))


if __name__ == "__main__":
    unittest.main()
