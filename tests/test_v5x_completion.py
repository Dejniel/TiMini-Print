from __future__ import annotations

import unittest

from timiniprint.printing.runtime.base import PreparedRuntimeContext, RuntimeController
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.job import ProtocolJob
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.steps import ProtocolStep
from timiniprint.printing.send import send_prepared_job


def _a1_frame(task_state: int, battery: int = 60, temp: int = 40) -> bytes:
    # 0xA1 status payload: [task_state, _, _, battery, temp, _, err_grp, err_code]
    payload = bytes([task_state, 0, 0, battery, temp, 0, 0, 0])
    return make_packet(0xA1, payload, ProtocolFamily.V5X)


class _FakeSession:
    """Minimal RuntimeSessionApi for driving wait_for_completion in tests."""

    def __init__(self, frames, *, can_wait: bool = True) -> None:
        self._frames = list(frames)  # each item: a 0xA1 frame, or None == quiet
        self._can_wait = can_wait
        self.debug: list[str] = []
        self.waits = 0

    def can_wait_for_notification(self) -> bool:
        return self._can_wait

    def report_debug(self, message: str) -> None:
        self.debug.append(message)

    def extract_prefixed_opcode(self, payload: bytes):
        prefix = ProtocolFamily.V5X.packet_prefix
        if len(payload) < len(prefix) + 1 or payload[: len(prefix)] != prefix:
            return None
        return payload[len(prefix)]

    def extract_prefixed_payload(self, packet: bytes):
        prefix = ProtocolFamily.V5X.packet_prefix
        if len(packet) < len(prefix) + 6 or packet[: len(prefix)] != prefix:
            return None
        length = packet[len(prefix) + 2] | (packet[len(prefix) + 3] << 8)
        start = len(prefix) + 4
        end = start + length
        if end + 2 > len(packet):
            return None
        return packet[start:end]

    async def wait_for_notification(self, label, match, *, timeout, required=False):
        self.waits += 1
        while self._frames:
            frame = self._frames.pop(0)
            if frame is None:
                return None  # quiet window elapsed
            if match(frame):
                return frame
        return None


def _fast_controller() -> V5XRuntimeController:
    c = V5XRuntimeController()
    c._COMPLETION_QUIET_S = 0.01
    c._COMPLETION_GRACE_S = 0.0
    c._COMPLETION_MAX_S = 5.0
    return c


class V5XWaitForCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_on_idle_status_frame(self) -> None:
        controller = _fast_controller()
        session = _FakeSession([_a1_frame(1), _a1_frame(0)])  # printing -> idle
        await controller.wait_for_completion(session, timeout=1.0)
        self.assertTrue(any("idle" in m for m in session.debug))
        self.assertEqual(session.waits, 2)

    async def test_returns_on_status_quiet(self) -> None:
        controller = _fast_controller()
        session = _FakeSession([_a1_frame(1), None])  # printing then quiet
        await controller.wait_for_completion(session, timeout=1.0)
        self.assertTrue(any("quiet" in m for m in session.debug))

    async def test_no_op_when_cannot_wait_for_notifications(self) -> None:
        controller = _fast_controller()
        session = _FakeSession([_a1_frame(0)], can_wait=False)
        await controller.wait_for_completion(session, timeout=1.0)
        self.assertEqual(session.waits, 0)

    async def test_never_sends_anything(self) -> None:
        # wait_for_completion must be purely passive: it must not expose/use any
        # send path (polling 0xA3 would feed paper). _FakeSession has no send_*.
        controller = _fast_controller()
        session = _FakeSession([None])
        await controller.wait_for_completion(session, timeout=1.0)  # must not raise


class _SpyController(RuntimeController):
    def __init__(self) -> None:
        self.completed = 0

    async def wait_for_completion(self, session, *, timeout: float) -> None:
        self.completed += 1


class _SendOnlyConnection:
    def __init__(self) -> None:
        self.sent: list[ProtocolJob] = []

    async def send(self, job: ProtocolJob) -> None:
        self.sent.append(job)


class _StandardPayloadConnection(_SendOnlyConnection):
    def __init__(self) -> None:
        super().__init__()
        self.standard_payloads: list[bytes] = []

    async def send_standard_payload(self, data: bytes) -> None:
        self.standard_payloads.append(bytes(data))


class SendPreparedJobCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_prepared_job_invokes_wait_for_completion(self) -> None:
        spy = _SpyController()
        job = ProtocolJob(payload=b"data", wait_for_completion=True)
        connection = _SendOnlyConnection()
        await send_prepared_job(
            object(),
            connection,
            job,
            runtime_context=PreparedRuntimeContext(runtime_controller=spy),
        )
        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(spy.completed, 1)

    async def test_send_prepared_job_invokes_wait_for_completion_after_protocol_steps(self) -> None:
        spy = _SpyController()
        job = ProtocolJob(
            steps=(ProtocolStep.send("bitmap", b"data"),),
            wait_for_completion=True,
        )
        connection = _StandardPayloadConnection()

        await send_prepared_job(
            object(),
            connection,
            job,
            runtime_context=PreparedRuntimeContext(runtime_controller=spy),
        )

        self.assertEqual(connection.standard_payloads, [b"data"])
        self.assertEqual(connection.sent, [])
        self.assertEqual(spy.completed, 1)

    async def test_send_prepared_job_without_controller_is_fine(self) -> None:
        job = ProtocolJob(payload=b"data")
        connection = _SendOnlyConnection()
        await send_prepared_job(object(), connection, job)
        self.assertEqual(len(connection.sent), 1)


if __name__ == "__main__":
    unittest.main()
