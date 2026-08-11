from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.phomemo_esc import PhomemoEscRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolStep


class _ReplyConnection:
    def __init__(self, replies: list[bytes]) -> None:
        self.replies = list(replies)
        self.sent_jobs: list[ProtocolJob] = []
        self.standard_payloads: list[bytes] = []
        self.wait_labels: list[str] = []

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))

    def can_wait_for_reply(self) -> bool:
        return True

    async def wait_for_reply(self, label, match, *, timeout, required=True):
        _ = timeout, required
        self.wait_labels.append(label)
        reply = self.replies.pop(0)
        if not match(reply):
            raise AssertionError(f"completion matcher rejected {reply.hex()}")
        return reply


class PhomemoEscRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.device = PrinterCatalog.load().device_from_model("printmaster_m110")

    def test_factory_limits_controller_to_documented_printmaster_variants(self) -> None:
        self.assertIsInstance(
            runtime_controller_for_device(self.device),
            PhomemoEscRuntimeController,
        )
        m120 = PrinterCatalog.load().device_from_model("printmaster_m120")
        self.assertIsInstance(
            runtime_controller_for_device(m120),
            PhomemoEscRuntimeController,
        )
        phomemo = PrinterCatalog.load().device_from_model("phomemo_m110")
        self.assertIsNone(runtime_controller_for_device(phomemo))

    async def test_payload_job_waits_for_inherited_completion(self) -> None:
        connection = _ReplyConnection([b"\x1a\x0f\x0c"])
        job = ProtocolJob(payload=b"raster", wait_for_completion=True)

        await send_prepared_job(self.device, connection, job, timeout=0.1)

        self.assertEqual(connection.sent_jobs, [job])
        self.assertEqual(connection.wait_labels, ["Print Master completion"])

    async def test_step_job_also_waits_for_inherited_completion(self) -> None:
        connection = _ReplyConnection([b"\x1a\x0f\x0c"])
        job = ProtocolJob(
            steps=(ProtocolStep.send("raster", b"raster"),),
            wait_for_completion=True,
        )

        await send_prepared_job(self.device, connection, job, timeout=0.1)

        self.assertEqual(connection.standard_payloads, [b"raster"])
        self.assertEqual(connection.wait_labels, ["Print Master completion"])


if __name__ == "__main__":
    unittest.main()
