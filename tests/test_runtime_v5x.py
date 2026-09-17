from __future__ import annotations

import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.printing import PrinterNotReadyError
from timiniprint.protocol import PrinterStatusCode
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import PrinterProtocol, ProtocolJob
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5x import (
    V5X_FINALIZE_PACKET,
    V5X_NOTIFY_START_PRINT_OK,
    V5X_NOTIFY_START_READY,
    build_sign_response,
)
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.steps import ProtocolStep
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class _Session:
    def __init__(
        self,
        controller: V5XRuntimeController,
        *,
        can_send_bulk: bool = True,
        auto_ready: bool = True,
    ) -> None:
        self.controller = controller
        self.can_send_bulk = can_send_bulk
        self.auto_ready = auto_ready
        self.events: list[tuple[str, bytes]] = []
        self.debug: list[str] = []
        self.last_reply: bytes | None = None

    def can_send_control_packet_wait_notification(self) -> bool:
        return True

    async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
        self.last_reply = None
        await self.send_control_packet(packet, timeout=timeout)
        reply = self.last_reply
        if reply is None or not match(reply):
            raise TimeoutError(label)
        return reply

    @staticmethod
    def make_packet(opcode: int, payload: bytes) -> bytes:
        return make_packet(opcode, payload, ProtocolFamily.V5X)

    @staticmethod
    def extract_prefixed_opcode(packet: bytes) -> int | None:
        prefix = ProtocolFamily.V5X.require_packet_prefix()
        if len(packet) < len(prefix) + 1 or not packet.startswith(prefix):
            return None
        return packet[len(prefix)]

    @staticmethod
    def extract_prefixed_payload(packet: bytes) -> bytes | None:
        prefix = ProtocolFamily.V5X.require_packet_prefix()
        if len(packet) < len(prefix) + 6 or not packet.startswith(prefix):
            return None
        length_offset = len(prefix) + 2
        payload_length = int.from_bytes(packet[length_offset : length_offset + 2], "little")
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

    @staticmethod
    def can_send_control_packet() -> bool:
        return True

    def can_send_bulk_payload(self) -> bool:
        return self.can_send_bulk

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = timeout
        self.events.append(("control", bytes(packet)))
        opcode = self.extract_prefixed_opcode(packet)
        if opcode == 0xAD and self.auto_ready:
            self.controller.handle_notification(self, V5X_NOTIFY_START_READY)
        elif opcode == 0xA9:
            self.last_reply = V5X_NOTIFY_START_PRINT_OK
            self.controller.handle_notification(self, V5X_NOTIFY_START_PRINT_OK)
        return True

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool:
        _ = timeout
        self.events.append(("bulk", bytes(data)))
        return True

    async def wait_for_notification(self, *args, **kwargs):
        raise AssertionError("pre-armed V5X acknowledgements should already be recorded")

    def report_debug(self, message: str) -> None:
        self.debug.append(message)

    def report_warning(self, *, short: str, detail: str) -> None:
        raise AssertionError(f"unexpected warning: {short}: {detail}")


def _page_payload() -> bytes:
    return (
        make_packet(0xA2, bytes([0x5D]), ProtocolFamily.V5X)
        + bytes.fromhex("2221A9000400010030000000")
        + bytes.fromhex("AA55AA55")
        + V5X_FINALIZE_PACKET
    )


class _ConnectInfoSession(_Session):
    def __init__(self, controller, reply=None, *, reply_on_send=False) -> None:
        super().__init__(controller)
        self.reply = reply
        self.reply_on_send = reply_on_send
        self.waits = []

    def can_wait_for_notification(self) -> bool:
        return True

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        sent = await super().send_control_packet(packet, timeout=timeout)
        if self.reply_on_send and self.reply is not None:
            self.controller.handle_notification(self, self.reply)
        return sent

    async def wait_for_notification(self, label, match, *, timeout, required):
        self.waits.append((label, timeout, required))
        if self.reply is not None:
            self.controller.handle_notification(self, self.reply)
            if match(self.reply):
                return self.reply
        return None


class _PrintSession(_Session):
    def __init__(self, controller, completion_reply=None) -> None:
        super().__init__(controller, auto_ready=False)
        self.completion_reply = completion_reply
        self.completion_waits = 0

    def can_wait_for_notification(self) -> bool:
        return True

    async def attach_runtime_controller(self, controller, *, timeout):
        if controller is not self.controller:
            self.controller = controller

    async def wait_for_notification(self, label, match, *, timeout, required):
        if label != "V5X print completion 0xa1" or required:
            raise AssertionError(f"unexpected notification wait: {label}")
        self.completion_waits += 1
        if self.completion_reply is not None:
            self.controller.handle_notification(self, self.completion_reply)
            if not match(self.completion_reply):
                raise AssertionError("completion reply did not match")
        return self.completion_reply


class V5XRuntimeConstructionTests(unittest.TestCase):
    def test_controller_construction_does_not_require_an_event_loop(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            controller = executor.submit(V5XRuntimeController).result(timeout=1)
        self.assertFalse(controller.debug_snapshot()["await_start_ready"])


class V5XRuntimeControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_job_without_aa_does_not_block_next_print(self) -> None:
        device = PrinterCatalog.load().detect_device("MXW01")
        job = ProtocolJob(
            steps=(ProtocolStep.send("page", _page_payload()),),
            wait_for_completion=True,
        )
        idle = make_packet(0xA1, bytes(8), ProtocolFamily.V5X)
        for reply in (idle, None):
            for _same_session in (True,):
                with self.subTest(reply=reply):
                    controller = V5XRuntimeController()
                    session = _PrintSession(controller, reply)
                    with patch.multiple(
                        V5XRuntimeController, _COMPLETION_MAX_S=0.01, _COMPLETION_GRACE_S=0.0,
                    ), patch(
                        "timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0,
                    ):
                        await send_prepared_job(PreparedPrinter(device, runtime_controller=controller), session, job, timeout=0.001)
                        await send_prepared_job(PreparedPrinter(device, runtime_controller=controller), session, job, timeout=0.001)
                    self.assertEqual(sum(kind == "bulk" for kind, _ in session.events), 2)
                    self.assertEqual(session.completion_waits, 2)
                    self.assertEqual(
                        [data[2] for kind, data in session.events if kind == "control"],
                        [0xA2, 0xA9, 0xAD, 0xA9, 0xAD],
                    )
                    self.assertFalse(controller.debug_snapshot()["await_start_ready"])

    async def test_b3_challenge_is_answered_each_time_without_network(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        challenges = (bytes(range(10)), bytes(reversed(range(10))), bytes(range(10)))
        with patch("timiniprint.printing.runtime.v5x.time.time", return_value=1788900001.234):
            for challenge in challenges:
                controller.handle_notification(session, make_packet(0xB3, challenge, ProtocolFamily.V5X))
            await asyncio.sleep(0)
        self.assertEqual(session.events, [("control", build_sign_response(challenge, timestamp_ms=1788900001234)) for challenge in challenges])
        self.assertTrue(controller.debug_snapshot()["mxw_sign_requested"])
        self.assertEqual(controller.debug_snapshot()["mxw_sign_responses_sent"], 3)

    async def test_b3_reply_is_sent_while_start_ack_is_pending(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        signing_done = asyncio.Event()
        original_send = session.send_control_packet
        async def send(packet, *, timeout):
            sent = await original_send(packet, timeout=timeout)
            if packet[2] == 0xB3:
                signing_done.set()
            return sent
        async def query(packet, *, label, match, timeout, required):
            session.events.append(("control", packet))
            controller.handle_notification(session, make_packet(0xB3, bytes(range(10)), ProtocolFamily.V5X))
            await asyncio.wait_for(signing_done.wait(), timeout)
            return V5X_NOTIFY_START_PRINT_OK
        session.send_control_packet = send
        session.send_control_packet_wait_notification = query
        await controller.send_protocol_steps(session, (ProtocolStep.send("page", _page_payload()),), timeout=0.2)
        self.assertEqual([(kind, data[2] if kind == "control" else None) for kind, data in session.events],
                         [("control", 0xA2), ("control", 0xA9), ("control", 0xB3), ("bulk", None), ("control", 0xAD)])

    async def test_b3_send_failure_is_reported_and_disconnect_cancels_pending_reply(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        session.report_warning = lambda **message: session.debug.append(message["short"])
        session.send_control_packet = AsyncMock(return_value=False)
        challenge = make_packet(0xB3, bytes(10), ProtocolFamily.V5X)
        controller.handle_notification(session, challenge)
        await asyncio.sleep(0)
        self.assertIn("V5X signing reply failed", session.debug)
        self.assertEqual(controller.debug_snapshot()["mxw_sign_responses_sent"], 0)
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def blocked(packet, *, timeout):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        session.send_control_packet = blocked
        controller.handle_notification(session, challenge)
        await asyncio.wait_for(started.wait(), 0.2)
        await controller.stop(session)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(controller.debug_snapshot()["mxw_sign_responses_sent"], 0)

    async def test_public_send_keeps_command_shaped_raster_on_bulk_channel(self) -> None:
        device = PrinterCatalog.load().detect_device("MXW01")
        raw = make_packet(0xA4, b"\x00\x00", ProtocolFamily.V5X) + bytes(38)
        pixels = [(value >> bit) & 1 for value in raw for bit in range(8)]
        job = PrinterProtocol(device).build_job(RasterSet.from_single(RasterBuffer(
            pixels=pixels, width=384, pixel_format=PixelFormat.BW1,
        )), is_text=False)
        controller = V5XRuntimeController()
        session = _Session(controller)
        with patch.object(controller, "wait_for_completion", new=AsyncMock()):
            await send_prepared_job(PreparedPrinter(device, runtime_controller=controller), session, job)
        self.assertEqual([p[2] for kind, p in session.events if kind == "control"], [0xA2, 0xA9, 0xAD])
        self.assertEqual([p for kind, p in session.events if kind == "bulk"], [raw])

    async def test_short_and_framed_a9_rejections_never_send_raster(self) -> None:
        for reply in (bytes.fromhex("2221a9000100"), make_packet(0xA9, b"\x03", ProtocolFamily.V5X)):
            with self.subTest(reply=reply):
                controller = V5XRuntimeController()
                session = _Session(controller)
                async def reject(packet, *, timeout):
                    session.events.append(("control", packet))
                    if packet[2] == 0xA9:
                        session.last_reply = reply
                        controller.handle_notification(session, reply)
                    return True
                session.send_control_packet = reject
                with self.assertRaisesRegex(PrinterNotReadyError, "start print was rejected") as caught:
                    await controller.send_protocol_steps(session, (ProtocolStep.send("page", _page_payload()),), timeout=0.1)
                self.assertEqual(caught.exception.reasons, (PrinterStatusCode.NOT_READY,))
                self.assertFalse(any(kind == "bulk" or p[2] == 0xAD for kind, p in session.events))

    async def test_density_settle_is_before_start_and_only_after_changes(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        controller.debug_update(print_head_type="diya")
        async def sleep(seconds):
            session.events.append(("sleep", seconds))
        with patch("timiniprint.printing.runtime.v5x.asyncio.sleep", new=sleep):
            for _ in range(2):
                await controller.send_protocol_steps(session, (ProtocolStep.send("page", _page_payload()),), timeout=0.1)
        self.assertEqual(
            [(kind, p[2] if kind == "control" else p if kind == "sleep" else len(p)) for kind, p in session.events],
            [("control", 0xA2), ("sleep", 0.06), ("control", 0xA9), ("bulk", 4), ("control", 0xAD),
             ("control", 0xA9), ("bulk", 4), ("control", 0xAD)],
        )

    async def test_high_coverage_settle_precedes_start(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        async def sleep(seconds):
            session.events.append(("sleep", seconds))
        with patch("timiniprint.printing.runtime.v5x.asyncio.sleep", new=sleep):
            await controller.send_protocol_steps(session, (ProtocolStep.send("page", _page_payload()),), timeout=0.1)
        self.assertEqual(session.events[1], ("sleep", 0.2))
        self.assertEqual(session.events[2][1][2], 0xA9)

    async def test_next_page_waits_for_fresh_aa_and_survives_controller_adoption(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        original_send = session.send_control_packet
        async def no_ready(packet, *, timeout):
            if packet[2] == 0xAD:
                session.events.append(("control", packet))
                return True
            return await original_send(packet, timeout=timeout)
        session.send_control_packet = no_ready
        step = ProtocolStep.send("page", _page_payload())
        with patch("timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0):
            await controller.send_protocol_steps(session, (step,), timeout=0.1)
            self.assertFalse(any(kind == "wait" for kind, _ in session.events))
            next_controller = controller
            session.controller = next_controller
            task = asyncio.create_task(next_controller.send_protocol_steps(session, (step,), timeout=0.1))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(len(session.events), 4)
            next_controller.handle_notification(session, V5X_NOTIFY_START_READY)
            await task
        self.assertEqual([kind for kind, _ in session.events], ["control", "control", "bulk", "control", "control", "bulk", "control"])

    async def test_next_page_allows_physical_print_time_beyond_command_timeout(self) -> None:
        controller = V5XRuntimeController()
        controller._COMPLETION_MAX_S = 0.2
        session = _Session(controller)
        send = session.send_control_packet
        ready_tasks = []
        async def delayed_ready():
            await asyncio.sleep(0.02)
            controller.handle_notification(session, V5X_NOTIFY_START_READY)
        async def delayed_finalize(packet, *, timeout):
            if packet[2] == 0xAD:
                session.events.append(("control", packet))
                ready_tasks.append(asyncio.create_task(delayed_ready()))
                return True
            return await send(packet, timeout=timeout)
        session.send_control_packet = delayed_finalize
        with patch("timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0):
            await controller.send_protocol_steps(session, (
                ProtocolStep.send("page 1", _page_payload()),
                ProtocolStep.send("page 2", _page_payload()),
            ), timeout=0.001)
        await asyncio.gather(*ready_tasks)
        self.assertEqual(sum(kind == "bulk" for kind, _ in session.events), 2)

    async def test_missing_next_page_ready_stops_before_the_next_page(self) -> None:
        controller = V5XRuntimeController()
        controller._COMPLETION_MAX_S = 0.01
        session = _Session(controller)
        send = session.send_control_packet
        async def no_ready(packet, *, timeout):
            if packet[2] == 0xAD:
                session.events.append(("control", packet))
                return True
            return await send(packet, timeout=timeout)
        session.send_control_packet = no_ready
        step = ProtocolStep.send("page", _page_payload())
        with patch("timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0):
            with self.assertRaisesRegex(TimeoutError, "start ready 0xaa"):
                await controller.send_protocol_steps(session, (step, step), timeout=0.001)
        self.assertEqual(sum(kind == "bulk" for kind, _ in session.events), 1)
        self.assertTrue(controller.debug_snapshot()["await_start_ready"])

    async def test_missing_or_unreadable_connect_info_never_reports_ready(self) -> None:
        replies = (
            None,
            bytes.fromhex("2221b10000"),
            make_packet(0xB1, b"", ProtocolFamily.V5X),
            make_packet(0xB1, b"\x00" * 9, ProtocolFamily.V5X),
            make_packet(0xB1, b"FW1.0.22", ProtocolFamily.V5X)[:-1],
        )
        for reply in replies:
            for reply_on_send in (False, True):
                with self.subTest(reply=reply, reply_on_send=reply_on_send):
                    controller = V5XRuntimeController()
                    session = _ConnectInfoSession(controller, reply, reply_on_send=reply_on_send)
                    with patch("timiniprint.printing.runtime.v5x.asyncio.sleep", new=AsyncMock()):
                        await controller.initialize_connection(session, mtu_size=20, timeout=0.1)
                        await controller.after_initialize(session, timeout=0.1)

                    state = controller.debug_snapshot()
                    self.assertFalse(state["connect_info_received"])
                    self.assertFalse(state["await_connect_info"])
                    self.assertEqual(state["firmware_version"], "")
                    self.assertNotIn("connect info ready: 0xb1", session.debug)
                    self.assertIn(
                        "V5X firmware info unavailable after the initial settle window", session.debug
                    )
                    self.assertEqual(session.waits, [("V5X connect info 0xb1", 0.1, False)])
                    if reply is not None:
                        self.assertTrue(any("no readable firmware payload" in line for line in session.debug))

    async def test_readable_connect_info_reports_ready_before_or_during_wait(self) -> None:
        for reply_on_send in (False, True):
            with self.subTest(reply_on_send=reply_on_send):
                controller = V5XRuntimeController()
                session = _ConnectInfoSession(
                    controller,
                    make_packet(0xB1, b"FW1.0.22\x00", ProtocolFamily.V5X),
                    reply_on_send=reply_on_send,
                )
                with patch("timiniprint.printing.runtime.v5x.asyncio.sleep", new=AsyncMock()):
                    await controller.initialize_connection(session, mtu_size=20, timeout=0.1)
                    await controller.after_initialize(session, timeout=0.1)

                state = controller.debug_snapshot()
                self.assertTrue(state["connect_info_received"])
                self.assertFalse(state["await_connect_info"])
                self.assertEqual(state["firmware_version"], "FW1.0.22")
                self.assertEqual(state["print_head_type"], "gaoya")
                self.assertEqual(session.debug.count("connect info ready: 0xb1"), 1)
                self.assertFalse(any("unavailable" in line for line in session.debug))
                self.assertEqual(len(session.waits), 0 if reply_on_send else 1)

                controller.handle_notification(session, bytes.fromhex("2221b10000"))
                self.assertEqual(controller.debug_snapshot()["firmware_version"], "FW1.0.22")
                self.assertTrue(controller.debug_snapshot()["connect_info_received"])
                self.assertEqual(session.debug.count("connect info ready: 0xb1"), 1)

    async def test_sends_each_page_as_command_bulk_finalize_sequence(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller)
        steps = (
            ProtocolStep.send("page 1", _page_payload()),
            ProtocolStep.send("page 2", _page_payload()),
        )

        with patch("timiniprint.printing.runtime.v5x.start_delay_ms", return_value=0):
            sent = await controller.send_protocol_steps(session, steps, timeout=0.2)

        self.assertTrue(sent)
        event_shape = [
            (kind, session.extract_prefixed_opcode(data) if kind == "control" else data)
            for kind, data in session.events
        ]
        self.assertEqual(
            event_shape,
            [
                ("control", 0xA2),
                ("control", 0xA9),
                ("bulk", bytes.fromhex("AA55AA55")),
                ("control", 0xAD),
                ("control", 0xA9),
                ("bulk", bytes.fromhex("AA55AA55")),
                ("control", 0xAD),
            ],
        )

    async def test_rejects_bulk_job_without_bulk_transport_capability(self) -> None:
        controller = V5XRuntimeController()
        session = _Session(controller, can_send_bulk=False)

        with self.assertRaisesRegex(RuntimeError, "V5X bulk payload send unavailable"):
            await controller.send_protocol_steps(
                session,
                (ProtocolStep.send("print data", _page_payload()),),
                timeout=0.2,
            )

        self.assertEqual(session.events, [])


if __name__ == "__main__":
    unittest.main()
