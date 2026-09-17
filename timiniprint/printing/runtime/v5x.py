from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from ...protocol.family import ProtocolFamily
from ...protocol.families.v5x import (
    V5X_CONNECT_INIT_PACKET,
    V5X_FINALIZE_PACKET,
    V5X_GET_SERIAL_PACKET,
    V5X_GRAY_MODE,
    V5X_NOTIFY_PAUSE_PACKETS,
    V5X_NOTIFY_RESUME_PACKETS,
    V5X_STATUS_POLL_PACKET,
    build_sign_response,
    split_print_stream,
)
from ...protocol.packet import make_packet, prefixed_packet_opcode, prefixed_packet_payload
from ...protocol.steps import ProtocolStepOperation
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from .base import RuntimeController
from .v5x_density import V5XJobContext, adjust_density_payload, start_delay_ms

_BIT_COUNTS = tuple(bin(value).count("1") for value in range(256))


@dataclass
class _V5XSessionState:
    task_state_name: str = "normal"
    last_density_payload: Optional[bytes] = None
    print_head_type: str = "gaoya"
    firmware_version: str = ""
    connect_info_received: bool = False
    device_serial: str = ""
    serial_valid: Optional[bool] = None
    last_a7_payload: bytes = b""
    last_a9_status: Optional[int] = None
    task_state: Optional[int] = None
    battery_level: Optional[int] = None
    temperature_c: Optional[int] = None
    error_group: Optional[int] = None
    error_code: Optional[int] = None
    last_error_signature: Optional[tuple[int, int]] = None
    first_status_monotonic: Optional[float] = None
    status_poll_ack_seen: bool = False
    last_ab_status: Optional[int] = None
    mxw_sign_requested: bool = False
    mxw_sign_responses_sent: int = 0
    pending_sign_responses: set[asyncio.Task] = field(default_factory=set)
    pending_get_serial: asyncio.Task | None = None
    pending_status_poll: asyncio.Task | None = None
    pending_command_acks: set[int] = field(default_factory=set)
    seen_command_acks: set[int] = field(default_factory=set)
    await_start_ready: bool = False
    start_ready_seen: bool = False
    start_ready_event: asyncio.Event | None = None
    await_connect_info: bool = False


class V5XRuntimeController(RuntimeController):
    # The MXW01 keeps physically printing (and does an end-of-job paper feed) for
    # several seconds after the last byte is sent. Closing the BLE link before it
    # finishes truncates the output. After a job we hold the link and wait for the
    # printer to go quiet/idle, then a short grace, before allowing disconnect.
    # NB: never poll 0xA3 to elicit status — that opcode moves paper; the printer
    # streams 0xA1 status frames on its own while printing, so we only listen.
    _COMPLETION_QUIET_S: float = 3.0
    _COMPLETION_GRACE_S: float = 3.0
    _COMPLETION_MAX_S: float = 60.0

    def __init__(self) -> None:
        self._state = _V5XSessionState()

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "task_state_name": self._state.task_state_name,
            "last_density_payload": self._state.last_density_payload,
            "print_head_type": self._state.print_head_type,
            "firmware_version": self._state.firmware_version,
            "connect_info_received": self._state.connect_info_received,
            "device_serial": self._state.device_serial,
            "serial_valid": self._state.serial_valid,
            "last_a7_payload": self._state.last_a7_payload,
            "last_a9_status": self._state.last_a9_status,
            "task_state": self._state.task_state,
            "battery_level": self._state.battery_level,
            "temperature_c": self._state.temperature_c,
            "error_group": self._state.error_group,
            "error_code": self._state.error_code,
            "last_error_signature": self._state.last_error_signature,
            "status_poll_ack_seen": self._state.status_poll_ack_seen,
            "last_ab_status": self._state.last_ab_status,
            "mxw_sign_requested": self._state.mxw_sign_requested,
            "mxw_sign_responses_sent": self._state.mxw_sign_responses_sent,
            "pending_command_ack_opcodes": sorted(self._state.pending_command_acks),
            "seen_command_ack_opcodes": sorted(self._state.seen_command_acks),
            "await_start_ready": self._state.await_start_ready,
            "start_ready_seen": self._state.start_ready_seen,
            "await_connect_info": self._state.await_connect_info,
        }

    def debug_update(self, **changes: object) -> None:
        for key, value in changes.items():
            if not hasattr(self._state, key):
                raise KeyError(f"Unknown V5X debug field '{key}'")
            setattr(self._state, key, value)

    async def initialize_connection(self, session, *, mtu_size: int, timeout: float) -> None:
        _ = mtu_size
        self._state.await_connect_info = session.can_wait_for_notification()
        await asyncio.sleep(0.2)
        sent = await session.send_control_packet(V5X_CONNECT_INIT_PACKET, timeout=timeout)
        if not sent:
            raise RuntimeError("V5X connect init send unavailable")

    async def after_initialize(self, session, *, timeout: float) -> None:
        if session.can_wait_for_notification():
            await self._wait_for_connect_info(session, min(timeout, 0.4))

    async def stop(self, session) -> None:
        self._cancel_pending_get_serial()
        self._cancel_pending_status_poll()
        pending = tuple(self._state.pending_sign_responses)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def send_protocol_steps(self, session, steps, *, timeout: float) -> bool:
        if any(step.operation is not ProtocolStepOperation.SEND for step in steps):
            return False
        split_jobs = tuple(split_print_stream(step.data) for step in steps)
        if not session.can_send_control_packet():
            raise RuntimeError("V5X control packet send unavailable")
        if any(split.bulk_payload for split in split_jobs) and not session.can_send_bulk_payload():
            raise RuntimeError("V5X bulk payload send unavailable")

        for split in split_jobs:
            await self._send_split_job(session, split, timeout=timeout)
        return True

    async def _send_split_job(self, session, split, *, timeout: float) -> None:
        context = self._build_job_context(session, split)
        density_changed = False
        for packet in split.commands:
            packet, density_updated = self._prepare_command(session, packet, context)
            if packet is None:
                continue
            density_changed = density_changed or density_updated
            await self._before_command(
                session,
                packet,
                context,
                timeout=timeout,
                density_updated=density_changed,
            )
            ack_opcode = self._arm_command_ack(session, packet)
            try:
                if ack_opcode is not None:
                    reply = await session.send_control_packet_wait_notification(
                        packet,
                        label="V5X command ack 0xa9",
                        match=lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5X) == 0xA9,
                        timeout=timeout,
                        required=True,
                    )
                    self._state.last_a9_status = self._extract_status_byte(session, reply or b"")
                    self._validate_command_ack(ack_opcode)
                else:
                    sent = await session.send_control_packet(packet, timeout=timeout)
                    if not sent:
                        raise RuntimeError("V5X control packet send unavailable")
                if density_updated:
                    self._state.last_density_payload = prefixed_packet_payload(packet, ProtocolFamily.V5X)
            finally:
                if ack_opcode is not None:
                    self._clear_command_ack_state(ack_opcode)

        if split.bulk_payload:
            sent = await session.send_bulk_payload(split.bulk_payload, timeout=timeout)
            if not sent:
                raise RuntimeError("V5X bulk payload send unavailable")

        for packet in split.trailing_commands:
            if packet == V5X_FINALIZE_PACKET:
                self._state.await_start_ready = True
                self._state.start_ready_seen = False
                self._state.start_ready_event = asyncio.Event()
            sent = await session.send_control_packet(packet, timeout=timeout)
            if not sent:
                raise RuntimeError("V5X trailing control packet send unavailable")

    def _build_job_context(self, session, split) -> V5XJobContext:
        is_gray = False
        for packet in split.commands:
            if prefixed_packet_opcode(packet, ProtocolFamily.V5X) != 0xA9:
                continue
            payload = prefixed_packet_payload(packet, ProtocolFamily.V5X)
            if payload is None:
                continue
            is_gray = len(payload) == 4 and payload[3] == V5X_GRAY_MODE
            break
        coverage_ratio = 0.0
        if split.bulk_payload and not is_gray:
            total_bits = len(split.bulk_payload) * 8
            if total_bits > 0:
                black_bits = sum(_BIT_COUNTS[chunk] for chunk in split.bulk_payload)
                coverage_ratio = black_bits / total_bits
        return V5XJobContext(coverage_ratio=coverage_ratio, is_gray=is_gray)

    def _prepare_command(
        self,
        session,
        packet: bytes,
        context: V5XJobContext,
    ) -> tuple[bytes | None, bool]:
        opcode = prefixed_packet_opcode(packet, ProtocolFamily.V5X)
        if opcode != 0xA2:
            return packet, False
        payload = prefixed_packet_payload(packet, ProtocolFamily.V5X)
        if payload is None:
            return packet, False
        adjusted_payload = adjust_density_payload(
            payload,
            context,
            temperature_c=self._state.temperature_c or 0,
            head_type=self._state.print_head_type,
        )
        if adjusted_payload != payload:
            packet = make_packet(0xA2, adjusted_payload, ProtocolFamily.V5X)
            payload = adjusted_payload
        if self._state.last_density_payload == payload:
            session.report_debug(f"skipping unchanged V5X density packet: {payload.hex()}")
            return None, False
        return packet, True

    async def _before_command(
        self,
        session,
        packet: bytes,
        context: V5XJobContext,
        *,
        timeout: float,
        density_updated: bool,
    ) -> None:
        opcode = prefixed_packet_opcode(packet, ProtocolFamily.V5X)
        if opcode in (0xA2, 0xA9):
            await self._wait_for_start_ready(session, timeout)
        if opcode == 0xA9:
            delay_ms = start_delay_ms(
                context,
                density_updated=density_updated,
                head_type=self._state.print_head_type,
            )
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)

    def _arm_command_ack(self, session, packet: bytes) -> int | None:
        opcode = prefixed_packet_opcode(packet, ProtocolFamily.V5X)
        if opcode != 0xA9:
            return None
        self._state.last_a9_status = None
        self._state.pending_command_acks.add(opcode)
        self._state.seen_command_acks.discard(opcode)
        return opcode

    async def wait_for_completion(self, session, *, timeout: float) -> None:
        # AA gates the next page of an unfinished job, not a separate print.
        # All pages have been sent when this completion hook runs.
        self._state.await_start_ready = False
        self._state.start_ready_seen = False
        self._state.start_ready_event = None

        # Hold the BLE link after the job is sent until the printer finishes. The
        # MXW01 streams 0xA1 status frames while printing; treat the job as done
        # when it reports idle (task_state=0) or after a quiet window with no
        # status, then wait a short grace before returning (caller disconnects).
        # We only listen — polling 0xA3 to elicit status would feed paper.
        if not session.can_wait_for_notification():
            return
        session.report_debug("V5X waiting for print completion before disconnect")
        deadline = time.monotonic() + self._COMPLETION_MAX_S
        capped = True
        while time.monotonic() < deadline:
            frame = await session.wait_for_notification(
                "V5X print completion 0xa1",
                lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5X) == 0xA1,
                timeout=self._COMPLETION_QUIET_S,
                required=False,
            )
            if frame is None:
                session.report_debug("V5X status quiet; print assumed finished")
                capped = False
                break
            raw = prefixed_packet_payload(frame, ProtocolFamily.V5X)
            if raw and raw[0] == 0x00:
                session.report_debug("V5X reported idle (task_state=0); print finished")
                capped = False
                break
        if capped:
            session.report_debug("V5X completion wait hit max cap; disconnecting anyway")
        if self._COMPLETION_GRACE_S > 0:
            await asyncio.sleep(self._COMPLETION_GRACE_S)

    def handle_notification(self, session, payload: bytes) -> None:
        if payload in V5X_NOTIFY_PAUSE_PACKETS:
            session.set_flow_paused(True, payload=payload)
            return
        if payload in V5X_NOTIFY_RESUME_PACKETS:
            session.set_flow_paused(False, payload=payload)
            return
        opcode = prefixed_packet_opcode(payload, ProtocolFamily.V5X)
        if opcode == 0xA7:
            self._update_info_from_a7(payload)
            self._mark_command_ack(session, 0xA7)
        elif opcode == 0xA1:
            self._update_status(session, payload)
        elif opcode == 0xA3:
            self._mark_status_poll_ack(session)
        elif opcode == 0xA6:
            self._schedule_get_serial(session)
        elif opcode == 0xAA:
            self._mark_start_ready(session)
        elif opcode == 0xA9:
            status = self._extract_status_byte(session, payload)
            self._state.last_a9_status = status
            self._mark_command_ack(session, 0xA9)
        elif opcode == 0xAB:
            self._update_ab_status(session, payload)
        elif opcode == 0xB0:
            self._update_head_type_from_b0(session, payload)
        elif opcode == 0xB1:
            self._update_info_from_b1(session, payload)
        elif opcode == 0xB2:
            self._schedule_status_poll(session)
        elif opcode == 0xB3:
            self._schedule_sign_response(session, payload)

    async def _wait_for_start_ready(self, session, timeout: float) -> None:
        if not self._state.await_start_ready:
            return
        try:
            if not self._state.start_ready_seen and self._state.start_ready_event is not None:
                session.report_debug("waiting for V5X next-page readiness: 0xaa")
                await asyncio.wait_for(
                    self._state.start_ready_event.wait(),
                    timeout=max(timeout, self._COMPLETION_MAX_S),
                )
        except asyncio.TimeoutError:
            raise TimeoutError("Timed out waiting for V5X start ready 0xaa") from None
        self._state.await_start_ready = False
        self._state.start_ready_seen = False

    async def _wait_for_connect_info(self, session, timeout: float) -> None:
        if not self._state.await_connect_info or self._state.connect_info_received:
            return
        await session.wait_for_notification(
            "V5X connect info 0xb1",
            lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5X) == 0xB1,
            timeout=timeout,
            required=False,
        )
        if not self._state.connect_info_received:
            session.report_debug("V5X firmware info unavailable after the initial settle window")
        self._state.await_connect_info = False

    def _mark_command_ack(self, session, opcode: int) -> None:
        if opcode not in self._state.pending_command_acks:
            return
        self._state.seen_command_acks.add(opcode)
        session.report_debug(f"command ack: 0x{opcode:02x}")

    def _clear_command_ack_state(self, opcode: int) -> None:
        self._state.pending_command_acks.discard(opcode)
        self._state.seen_command_acks.discard(opcode)

    def _mark_start_ready(self, session) -> None:
        if not self._state.await_start_ready:
            return
        self._state.start_ready_seen = True
        if self._state.start_ready_event is not None:
            self._state.start_ready_event.set()
        session.report_debug("start ready: 0xaa")

    def _mark_connect_info(self, session) -> None:
        if not self._state.await_connect_info:
            return
        self._state.await_connect_info = False
        session.report_debug("connect info ready: 0xb1")

    def _schedule_status_poll(self, session) -> None:
        if self._state.pending_status_poll is not None and not self._state.pending_status_poll.done():
            return
        if not session.can_send_control_packet():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._state.pending_status_poll = loop.create_task(self._send_status_poll(session))
        self._state.pending_status_poll.add_done_callback(lambda _task: setattr(self._state, "pending_status_poll", None))

    def _schedule_get_serial(self, session) -> None:
        if self._state.pending_get_serial is not None and not self._state.pending_get_serial.done():
            return
        if not session.can_send_control_packet():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._state.pending_get_serial = loop.create_task(self._send_command(session, V5X_GET_SERIAL_PACKET))
        self._state.pending_get_serial.add_done_callback(lambda _task: setattr(self._state, "pending_get_serial", None))

    async def _send_status_poll(self, session) -> None:
        await asyncio.sleep(0.7)
        await self._send_command(session, V5X_STATUS_POLL_PACKET)
        session.report_debug("scheduled status poll: 0xa3")

    async def _send_command(self, session, packet: bytes) -> None:
        await session.send_control_packet(packet, timeout=1.0)

    def _cancel_pending_get_serial(self) -> None:
        if self._state.pending_get_serial is None:
            return
        self._state.pending_get_serial.cancel()
        self._state.pending_get_serial = None

    def _cancel_pending_status_poll(self) -> None:
        if self._state.pending_status_poll is None:
            return
        self._state.pending_status_poll.cancel()
        self._state.pending_status_poll = None

    def _validate_command_ack(self, opcode: int) -> None:
        if opcode != 0xA9:
            return
        status = self._state.last_a9_status
        if status is None:
            raise RuntimeError("V5X start print response did not include a status byte")
        if status != 0x00:
            raise PrinterNotReadyError(
                f"V5X start print was rejected (status=0x{status:02x})",
                PrinterStatusCode.NOT_READY,
            )

    def _update_info_from_a7(self, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if raw is None:
            return
        self._state.last_a7_payload = raw
        serial_hex = raw[:6].hex()
        self._state.device_serial = serial_hex
        self._state.serial_valid = bool(serial_hex) and serial_hex not in {"000000000000", "ffffffffffff"}

    def _extract_status_byte(self, session, payload: bytes) -> Optional[int]:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if raw:
            return raw[0]
        prefix = ProtocolFamily.V5X.require_packet_prefix()
        if len(payload) == 6 and payload.startswith(prefix):
            return payload[-2]
        return None

    def _update_status(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if raw is None or len(raw) < 8:
            return
        self._state.task_state = raw[0]
        self._state.task_state_name = self._task_state_name(raw[0])
        self._state.battery_level = raw[3]
        self._state.temperature_c = raw[4]
        self._state.error_group = raw[6]
        self._state.error_code = raw[7]
        now = time.monotonic()
        if self._state.first_status_monotonic is None:
            self._state.first_status_monotonic = now
        elapsed = now - self._state.first_status_monotonic
        session.report_debug(
            f"V5X status +{elapsed:5.2f}s: state={self._state.task_state_name} "
            f"battery={raw[3]} temp={raw[4]}C err={raw[6]}/{raw[7]}"
        )
        self._handle_error_state(session, raw[6], raw[7])

    @staticmethod
    def _task_state_name(task_state: int) -> str:
        if task_state == 0x00:
            return "normal"
        if task_state == 0x01:
            return "printing"
        if task_state == 0x02:
            return "feeding"
        if task_state == 0x03:
            return "retracting"
        return f"0x{task_state:02x}"

    def _handle_error_state(self, session, error_group: int, error_code: int) -> None:
        signature = (error_group, error_code)
        if signature == (0x00, 0x00):
            self._state.last_error_signature = signature
            return
        if self._state.last_error_signature == signature:
            return
        self._state.last_error_signature = signature
        session.report_warning(
            short="V5X printer reported an error status",
            detail=(
                f"Task={self._state.task_state_name}, "
                f"error_group=0x{error_group:02x}, error_code=0x{error_code:02x}."
            ),
        )

    def _mark_status_poll_ack(self, session) -> None:
        self._state.status_poll_ack_seen = True
        session.report_debug("V5X status poll acknowledged: 0xa3")

    def _update_ab_status(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if not raw:
            return
        self._state.last_ab_status = raw[-1]

    def _schedule_sign_response(self, session, payload: bytes) -> None:
        self._state.mxw_sign_requested = True
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if raw is None or len(raw) < 10:
            session.report_warning(
                short="Invalid V5X signing challenge",
                detail="The B3 response requires ten challenge bytes; no reply was sent.",
            )
            return
        packet = build_sign_response(raw[:10], timestamp_ms=int(time.time() * 1000))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            session.report_warning(
                short="V5X signing reply unavailable",
                detail="No active runtime event loop for the B3 response.",
            )
            return
        task = loop.create_task(self._send_sign_response(session, packet))
        self._state.pending_sign_responses.add(task)
        task.add_done_callback(self._state.pending_sign_responses.discard)

    async def _send_sign_response(self, session, packet: bytes) -> None:
        try:
            if not await session.send_control_packet(packet, timeout=1.0):
                raise RuntimeError("V5X control packet send unavailable")
        except Exception as exc:
            session.report_warning(
                short="V5X signing reply failed",
                detail=str(exc),
            )
            return
        self._state.mxw_sign_responses_sent += 1
        session.report_debug(
            "V5X local signing response sent: 0xb3"
        )

    def _update_head_type_from_b0(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        if not raw:
            return
        value = raw[0]
        if value == 0x01:
            self._state.print_head_type = "gaoya"
        elif value == 0xFF:
            self._state.print_head_type = "weishibie"
        else:
            self._state.print_head_type = "diya"

    def _update_info_from_b1(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5X)
        firmware = raw.decode("ascii", errors="ignore").rstrip("\x00") if raw else ""
        if not firmware:
            session.report_debug(
                "V5X connect info 0xb1 has no readable firmware payload: "
                f"bytes={len(payload)} head={payload[:24].hex()}"
            )
            return
        self._state.connect_info_received = True
        self._state.firmware_version = firmware
        marker = firmware[-1]
        if marker == "2":
            self._state.print_head_type = "gaoya"
        elif marker == "1":
            self._state.print_head_type = "diya"
        else:
            self._state.print_head_type = "weishibie"
        session.report_debug(
            f"V5X firmware: version={firmware}, print_head_type={self._state.print_head_type}"
        )
        self._mark_connect_info(session)
