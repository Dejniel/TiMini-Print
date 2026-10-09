from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from ...protocol.family import ProtocolFamily
from ...protocol.families.v5c import (
    V5C_CONNECT_INIT_PACKET,
    V5C_NOTIFY_PAUSE,
    V5C_NOTIFY_RESUME,
    V5C_QUERY_STATUS_PACKET,
    V5CPrintCapabilities,
)
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from ...protocol.packet import PrefixedPacketStreamDecoder, prefixed_packet_payload
from ...protocol.steps import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep, ProtocolStepOperation
from ..step_execution import execute_protocol_step
from .base import PreparedPrinter, RuntimeController


@dataclass
class _V5CSessionState:
    status_code: Optional[int] = None
    status_name: str = "unknown"
    is_charging: bool = False
    print_complete_seen: bool = False
    printing: bool = False
    completion: asyncio.Event = field(default_factory=asyncio.Event)
    fatal_error: PrinterNotReadyError | None = None
    max_print_height: Optional[int] = None
    device_serial: str = ""
    serial_valid: Optional[bool] = None
    last_auth_payload: bytes = b""
    last_error_status: Optional[int] = None


class V5CRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._state = _V5CSessionState()
        self._decoder = PrefixedPacketStreamDecoder(ProtocolFamily.V5C)

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "status_code": self._state.status_code,
            "status_name": self._state.status_name,
            "is_charging": self._state.is_charging,
            "print_complete_seen": self._state.print_complete_seen,
            "max_print_height": self._state.max_print_height,
            "device_serial": self._state.device_serial,
            "serial_valid": self._state.serial_valid,
            "last_auth_payload": self._state.last_auth_payload,
            "last_error_status": self._state.last_error_status,
        }

    def debug_update(self, **changes: object) -> None:
        for key, value in changes.items():
            if not hasattr(self._state, key):
                raise KeyError(f"Unknown V5C debug field '{key}'")
            setattr(self._state, key, value)

    async def initialize_connection(self, session, *, mtu_size: int, timeout: float) -> None:
        _ = mtu_size
        await asyncio.sleep(0.6)
        if session.can_query_control_packet() or session.can_send_control_packet_wait_notification():
            def height_reply(data: bytes) -> bool:
                return any(frame.opcode == 0xAA and len(frame.payload) >= 4
                           for frame in PrefixedPacketStreamDecoder(ProtocolFamily.V5C).feed(data))

            step = ProtocolStep.query(
                "V5C maximum height", V5C_CONNECT_INIT_PACKET,
                expect=ProtocolReplyExpectation.NONE,
                reply_matcher=ProtocolReplyMatcher(complete=height_reply),
                timeout_sec=min(timeout, 0.4),
            )
            try:
                reply = await execute_protocol_step(session, step, timeout=timeout, log_prefix="V5C")
            except TimeoutError:
                reply = None
                session.report_debug("V5C height probe timed out; retaining fallback height")
            if reply is not None:
                for frame in PrefixedPacketStreamDecoder(ProtocolFamily.V5C).feed(reply):
                    if frame.opcode == 0xAA:
                        self._update_max_print_height(session, frame.payload)
        elif not await session.send_control_packet(V5C_CONNECT_INIT_PACKET, timeout=timeout):
            raise RuntimeError("V5C connect init send unavailable")

    async def prepare(self, device, session, *, timeout: float) -> PreparedPrinter:
        return PreparedPrinter(
            device, self, V5CPrintCapabilities(max_gray_height=self._state.max_print_height or 800),
        )

    @asynccontextmanager
    async def job_scope(self, session, job, *, timeout: float):
        self._raise_printer_error()
        self._state.completion.clear()
        self._state.print_complete_seen = False
        self._state.printing = True
        try:
            yield
            self._raise_printer_error()
        finally:
            self._state.printing = False

    async def before_write(self, session, *, size: int, timeout: float) -> None:
        if self._state.printing:
            self._raise_printer_error()

    async def wait_for_completion(self, session, *, timeout: float) -> None:
        self._raise_printer_error()
        if not session.can_observe_replies():
            session.report_warning(
                short="V5C completion unavailable",
                detail="Print bytes were sent, but this connection cannot observe printer completion.",
            )
            return
        try:
            await asyncio.wait_for(self._state.completion.wait(), timeout=max(60.0, timeout))
        except asyncio.TimeoutError:
            raise TimeoutError("Timed out waiting for V5C print completion") from None
        self._raise_printer_error()

    def _raise_printer_error(self) -> None:
        if self._state.fatal_error is not None:
            raise self._state.fatal_error

    def handle_notification(self, session, payload: bytes) -> None:
        for frame in self._decoder.feed(payload):
            if frame.raw == V5C_NOTIFY_PAUSE:
                session.set_flow_paused(True, payload=frame.raw)
            elif frame.raw == V5C_NOTIFY_RESUME:
                session.set_flow_paused(False, payload=frame.raw)
            elif frame.opcode == 0xA1:
                self._update_status(session, frame.raw)
            elif frame.opcode == 0xAA:
                self._update_max_print_height(session, frame.payload)
            elif frame.opcode in (0xA8, 0xA9):
                self._update_identity(frame.raw, frame.opcode)

    async def send_protocol_steps(self, session, steps, *, timeout: float) -> bool:
        _ = timeout
        if not session.can_send_standard_payload():
            return False
        if any(step.operation is not ProtocolStepOperation.SEND for step in steps):
            return False
        # Named V5C steps remain one standard stream; their logical boundary is
        # not a BLE write boundary.
        data = b"".join(step.data for step in steps)
        await session.send_standard_payload(data)
        self._raise_printer_error()
        return True

    def _update_status(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5C)
        if not raw:
            return
        status = raw[0]
        self._state.status_code = status
        self._state.status_name = self._status_name(status)
        self._state.is_charging = status in (0x10, 0x11)
        if status == 0x80:
            self._state.print_complete_seen = False
        elif status == 0x00:
            self._state.print_complete_seen = True
            if self._state.printing:
                self._state.completion.set()
        self._handle_status(session, status)

    @staticmethod
    def _status_name(status: int) -> str:
        if status == 0x00:
            return "normal"
        if status == 0x80:
            return "printing"
        if status in (0x10, 0x11):
            return "charging"
        if status in (0x01, 0x02, 0x03):
            return "attention"
        if status == 0x04:
            return "overheat"
        if status == 0x08:
            return "low_power"
        return f"0x{status:02x}"

    def _handle_status(self, session, status: int) -> None:
        if status in (0x00, 0x80, 0x10, 0x11):
            if not self._state.printing:
                self._state.last_error_status = None
                self._state.fatal_error = None
            return
        if self._state.last_error_status == status:
            return
        self._state.last_error_status = status
        if status in (0x01, 0x02, 0x03):
            short = "V5C printer reported an attention state"
            reason = PrinterStatusCode.PAPER_OUT
        elif status == 0x04:
            short = "V5C printer reported an overheat state"
            reason = PrinterStatusCode.OVERHEATED
        elif status == 0x08:
            short = "V5C printer reported a low-power state"
            reason = PrinterStatusCode.LOW_BATTERY
        else:
            short = "V5C printer reported an error status"
            reason = PrinterStatusCode.PRINTER_ERROR
        self._state.fatal_error = PrinterNotReadyError(f"{short}: status=0x{status:02x}", reason)
        if self._state.printing:
            self._state.completion.set()
        session.report_warning(short=short, detail=f"status=0x{status:02x} ({self._state.status_name}).")

    def _update_max_print_height(self, session, payload: bytes) -> None:
        if len(payload) < 4:
            return
        maximum = int.from_bytes(payload[2:4], "little")
        if maximum > 0:
            self._state.max_print_height = maximum
            session.report_debug(f"V5C maximum gray height: {maximum} rows")

    def _update_identity(self, payload: bytes, opcode: int) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5C)
        if raw is None:
            return
        self._state.last_auth_payload = raw
        if opcode == 0xA8:
            self._state.device_serial = ""
            self._state.serial_valid = None
            return
        serial_hex = raw[:8].hex()
        self._state.device_serial = serial_hex
        self._state.serial_valid = bool(serial_hex) and int(serial_hex, 16) != 0
