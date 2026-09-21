"""Phomemo reply/state lifecycle shared by direct and buffered page recipes."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from ...devices import PrinterDevice

from ...protocol import PaperMode, ProtocolJob, ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from ...protocol.families.phomemo_esc.flow import (
    PhomemoCapabilities, PhomemoPageStep,
)
from ...protocol.families.phomemo_esc.replies import PhomemoReplyDecoder, phomemo_reply
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from ..step_execution import execute_protocol_step
from .base import PreparedPrinter, RuntimeController, RuntimeSessionApi


class PhomemoRuntimeController(RuntimeController):
    COMPLETION_TIMEOUT_SEC = 180.0
    INFO_REPLY_TIMEOUT_SEC = 0.5

    def __init__(
        self, *, ribbon_status: bool = False, cutter_status: bool = False,
        ignore_cancel: bool = False, paginated: bool = False,
        paper_present_delay_sec: float = 0.0, paper_recovery_delay_sec: float = 0.0,
        require_serial: bool = False, require_chip: bool = False,
        query_extended_info: bool = True,
    ) -> None:
        self._require_serial = require_serial
        self._require_chip = require_chip
        self._query_extended_info = query_extended_info
        self._legacy_chip_reply = False
        self._capabilities = PhomemoCapabilities()
        self._firmware: str | None = None
        self._battery: int | None = None
        self._battery_low: bool | None = None
        self._shutdown_value: int | None = None
        self._charging: bool | None = None
        self._ribbon_status = ribbon_status
        self._cutter_status = cutter_status
        self._ignore_cancel = ignore_cancel
        self._paginated = paginated
        self._paper_mode = PaperMode.A4_SHEET if paginated else PaperMode.PLAIN
        self._paper_present_delay_sec = paper_present_delay_sec
        self._paper_recovery_delay_sec = paper_recovery_delay_sec
        self._decoder = PhomemoReplyDecoder()
        self._lock = threading.RLock()
        self._active = False
        self._error: Exception | None = None
        self._expected = 0
        self._received = 0
        self._sequence = 0
        self._ignore_pending_result = False
        self._paper_out = False
        self._overheated = False
        self._cover_open = False
        self._cutter_pressed = False
        self._ribbon_fault = False
        self._refresh_ribbon = False
        self._ready_at = 0.0

    async def prepare(self, device: PrinterDevice, session: RuntimeSessionApi, *, timeout: float) -> PreparedPrinter:
        self._legacy_chip_reply = device.display_name == "T02"
        if device.display_name == "M02H":
            await session.send_standard_payload(b"\x1b\x40")
        if session.can_query_control_packet() or session.can_send_control_packet_wait_notification():
            # Metadata is optional unless the raster recipe requires a serial.
            info_timeout = min(timeout, self.INFO_REPLY_TIMEOUT_SEC)
            queries = (
                (0x38, (0x17, 0x16) if self._legacy_chip_reply else 0x17),
                (0x07, 0x07), (0x09, 0x09), (0x08, 0x08), (0x0E, 0x04),
            )
            if self._query_extended_info:
                queries += ((0x63, None), (0x5E, None), (0x56, None), (0x51, None))
            for command, opcode in queries:
                required = (command == 0x08 and self._require_serial) or (command == 0x38 and self._require_chip)
                await self.query_reply(
                    session, bytes((0x1F, 0x11, command)), opcode,
                    timeout=timeout if required else info_timeout, required=required,
                )
            for command, opcode in ((b"\x1f\x11\x12", 0x05), (b"\x1f\x11\x11", 0x06)):
                await self.query_reply(session, command, opcode, timeout=timeout, required=False)
        if self._require_serial and len(self._capabilities.serial_number) != 15:
            raise RuntimeError("Phomemo requires a 15-byte serial number before building the raster")
        if self._require_chip and self._capabilities.chip_type is None:
            raise RuntimeError("Phomemo requires chip identification before selecting sheet media")
        if self._ribbon_status:
            await self._query_ribbon(session, timeout=timeout)
        return PreparedPrinter(device, self, self._capabilities)

    async def query_reply(
        self, session: RuntimeSessionApi, command: bytes, opcode: int | tuple[int, ...] | None,
        *, timeout: float, required: bool = True,
    ) -> bytes | None:
        await asyncio.sleep(0.1)
        reply = await execute_protocol_step(session, ProtocolStep.query(
            "Phomemo query " + command.hex(" "), command,
            expect=ProtocolReplyExpectation.NONE,
            reply_matcher=ProtocolReplyMatcher(
                complete=lambda data: phomemo_reply(data, opcode) is not None,
            ),
            reply_required=required, include_in_payload=False,
        ), timeout=timeout)
        if reply and not session.can_observe_replies():
            self.handle_notification(session, reply)
        return phomemo_reply(reply or b"", opcode)

    async def _query_ribbon(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        self._refresh_ribbon = False
        await self.query_reply(session, b"\x1f\x11\x46", 0x20, timeout=timeout)
        if self._ribbon_fault:
            raise PrinterNotReadyError("Phomemo reported a ribbon fault", PrinterStatusCode.RIBBON_ERROR)

    @asynccontextmanager
    async def job_scope(
        self, session: RuntimeSessionApi, job: ProtocolJob, *, timeout: float,
    ) -> AsyncIterator[None]:
        with self._lock:
            if self._active:
                raise RuntimeError("Phomemo already has an active job")
            if job.steps and isinstance(job.steps[0], PhomemoPageStep):
                self._paginated = job.steps[0].paginated
                self._paper_mode = job.steps[0].paper_mode
            self._active = True
            self._error = None
            self._expected = 0
            self._received = 0
            self._ignore_pending_result = self._decoder.has_pending_frame
        try:
            if not job.steps:
                # Raw callers still get fault checking and a passive result wait.
                await self._prepare_send(session, int(job.wait_for_completion), timeout=timeout)
            yield
            self._raise_error()
        finally:
            with self._lock:
                self._active = False
                self._expected = 0

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        with self._lock:
            for frame in self._decoder.feed(payload):
                old_result = self._ignore_pending_result
                self._ignore_pending_result = False
                self._sequence += 1
                opcode, value = frame[:2]
                session.report_debug("Phomemo reply: " + frame.hex(" "))
                fault = None
                if opcode == 0x03 and value in (0xA8, 0xA9):
                    self._overheated = value == 0xA9
                elif opcode == 0x04:
                    self._battery_low = value in (0xA1, 0xA2, 0xA3)
                    self._battery = {0xA1: 10, 0xA2: 5, 0xA3: 3}.get(value, int.from_bytes(frame[1:2], "big", signed=True))
                elif opcode == 0x07:
                    self._firmware = ".".join(str(v if v < 128 else v - 256) for v in frame[1:])
                elif opcode == 0x08:
                    self._capabilities = replace(self._capabilities, serial_number=frame[1:])
                elif opcode == 0x09:
                    self._shutdown_value = 5 * int.from_bytes(frame[1:2], "big", signed=True)
                elif opcode == 0x17 or (opcode == 0x16 and self._legacy_chip_reply):
                    self._capabilities = replace(self._capabilities, chip_type=value)
                elif opcode == 0x35:
                    self._charging = value == 0x02
                elif opcode == 0x3B:
                    d0, d1, d2 = frame[2:5]
                    self._capabilities = replace(
                        self._capabilities,
                        reported_supports_gray=bool(d0 & 4),
                        reported_gray_levels=(1 << (d1 >> 4)) if d1 >> 4 else 16,
                        double_dpi=bool(d1 & 8), charging_print_restricted=bool(d1 & 2),
                        multiple_densities=bool(d2 & 1), label_workshop=bool(d2 & 8),
                        paper_sensor_suppression=bool(d2 & 4),
                    )
                elif opcode == 0x05 and value in (0x98, 0x99):
                    self._cover_open = value == 0x99
                    if self._cover_open:
                        fault = (PrinterStatusCode.COVER_OPEN, "cover opened")
                    elif self._ribbon_status:
                        self._refresh_ribbon = True
                elif opcode == 0x06:
                    was_out = self._paper_out
                    self._paper_out = value == 0x88
                    if self._paper_out and not self._paginated:
                        fault = (PrinterStatusCode.PAPER_OUT, "out of paper")
                    elif not self._paper_out:
                        delay = max(
                            self._paper_present_delay_sec,
                            self._paper_recovery_delay_sec if was_out else 0.0,
                        )
                        self._ready_at = max(self._ready_at, time.monotonic() + delay)
                        if self._ribbon_status:
                            self._refresh_ribbon = True
                elif opcode == 0x0B and value == 0xB8 and not self._ignore_cancel:
                    fault = (PrinterStatusCode.NOT_READY, "job cancelled at the printer")
                elif opcode == 0x0E and self._cutter_status and value in (0xB8, 0xB9):
                    self._cutter_pressed = value == 0xB8
                    if self._cutter_pressed:
                        fault = (PrinterStatusCode.CUTTER_ERROR, "cutter pressed")
                elif opcode == 0x20 and self._ribbon_status:
                    self._ribbon_fault = value in (0x01, 0x03)
                    if self._ribbon_fault:
                        fault = (PrinterStatusCode.RIBBON_ERROR, "ribbon fault")
                elif opcode == 0x0F and not old_result:
                    if value != 0x0C:
                        fault = (PrinterStatusCode.PRINTER_ERROR, "print failed (0x%02X)" % value)
                    elif self._active and self._received < self._expected:
                        self._received += 1
                if fault and self._active and self._error is None:
                    self._error = PrinterNotReadyError("Phomemo: " + fault[1], fault[0])

    def _raise_error(self) -> None:
        with self._lock:
            if self._error is not None:
                raise self._error

    async def _read_change(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        observed = session.can_observe_replies()
        sequence = self._sequence
        def changed(data: bytes) -> bool:
            return self._sequence != sequence if observed else bool(PhomemoReplyDecoder().feed(data))
        reply = await session.wait_for_reply(
            "Phomemo status", changed, timeout=timeout, required=False,
        )
        if reply and not observed:
            self.handle_notification(session, reply)

    async def _check_ready(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        deadline = time.monotonic() + self.COMPLETION_TIMEOUT_SEC
        reported_wait = False
        while True:
            self._raise_error()
            if self._refresh_ribbon:
                await self._query_ribbon(session, timeout=timeout)
            for condition, code, message in (
                (self._cover_open, PrinterStatusCode.COVER_OPEN, "cover open"),
                (self._cutter_pressed, PrinterStatusCode.CUTTER_ERROR, "cutter pressed"),
                (self._ribbon_fault, PrinterStatusCode.RIBBON_ERROR, "ribbon fault"),
            ):
                if condition:
                    raise PrinterNotReadyError("Phomemo: " + message, code)
            if self._paper_out and not self._paginated:
                raise PrinterNotReadyError("Phomemo: out of paper", PrinterStatusCode.PAPER_OUT)
            if self._paper_out or self._overheated:
                if not reported_wait:
                    session.report_status("Phomemo: waiting for paper or print-head cooling")
                    reported_wait = True
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not session.can_wait_for_reply():
                    code = PrinterStatusCode.PAPER_OUT if self._paper_out else PrinterStatusCode.OVERHEATED
                    raise PrinterNotReadyError("Phomemo did not become ready", code)
                await self._read_change(session, timeout=min(1.0, remaining))
                continue
            self._raise_error()
            delay = self._ready_at - time.monotonic() if self._paper_mode is PaperMode.A4_SHEET else 0.0
            if delay <= 0:
                return
            if time.monotonic() >= deadline:
                raise PrinterNotReadyError("Phomemo did not become ready", PrinterStatusCode.NOT_READY)
            await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            # A paper/heat change during cooldown must be checked before sending.

    async def _prepare_send(self, session: RuntimeSessionApi, count: int, *, timeout: float) -> None:
        with self._lock:
            self._expected = 0
        if session.can_wait_for_reply() and not session.can_observe_replies():
            # Consume previous query replies before a new raster can produce a result.
            pending = await session.wait_for_reply(
                "Phomemo previous replies", bool, timeout=0.0, required=False,
            )
            if pending:
                self.handle_notification(session, pending)
        # Paper recovery, cooling and ribbon queries can receive old results.
        # Arm only after readiness, immediately before sending the new raster.
        await self._check_ready(session, timeout=timeout)
        with self._lock:
            self._raise_error()
            self._received = 0
            self._expected = count
            self._ignore_pending_result = self._decoder.has_pending_frame

    async def send_protocol_steps(
        self, session: RuntimeSessionApi, steps: tuple[ProtocolStep, ...], *, timeout: float,
    ) -> bool:
        if not steps or not all(isinstance(step, PhomemoPageStep) for step in steps):
            raise ValueError("Phomemo requires page steps with media and completion metadata")
        if any(step.buffered != steps[0].buffered for step in steps):
            raise ValueError("Cannot mix direct and buffered Phomemo pages")
        self._paginated = steps[0].paginated
        self._paper_mode = steps[0].paper_mode
        if steps[0].buffered:
            await self._check_ready(session, timeout=timeout)
            state = await self.query_reply(session, b"\x1f\x11\x2f", 0x1D, timeout=3.0)
            if state != b"\x00":
                raise PrinterNotReadyError("Phomemo is busy", PrinterStatusCode.BUSY)
            await self._prepare_send(session, len(steps), timeout=timeout)
            # One setup and one idle query for the assembled buffer.
            data = b"".join(step.data for step in steps)
            await session.send_standard_payload(data)
            return True
        for step in steps:
            self._paginated = step.paginated
            self._paper_mode = step.paper_mode
            await self._prepare_send(
                session, int(step.wait_for_result and step.completion_delay_sec is None), timeout=timeout,
            )
            await session.send_standard_payload(step.data)
            self._raise_error()
            if step.completion_delay_sec is not None:
                # This is a local estimate, never a successful printer ACK.
                deadline = time.monotonic() + step.completion_delay_sec
                while time.monotonic() < deadline:
                    self._raise_error()
                    if session.can_wait_for_reply():
                        await self._read_change(session, timeout=min(1.0, deadline - time.monotonic()))
                    else:
                        await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            elif step.paginated:
                await self.wait_for_completion(session, timeout=timeout)
        return True

    async def wait_for_completion(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        if not self._expected:
            self._raise_error()
            return
        if not session.can_wait_for_reply():
            session.report_warning(
                short="Phomemo completion unavailable",
                detail="The payload was sent, but this connection cannot receive passive print results.",
            )
            self._expected = 0
            return
        deadline = time.monotonic() + self.COMPLETION_TIMEOUT_SEC
        while self._received < self._expected:
            self._raise_error()
            if self._refresh_ribbon:
                await self._query_ribbon(session, timeout=timeout)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Phomemo print completion timed out")
            await self._read_change(session, timeout=min(1.0, remaining))
        self._raise_error()
        self._expected = 0

    async def stop(self, session: RuntimeSessionApi) -> None:
        with self._lock:
            self._error = RuntimeError("Phomemo connection closed during printing")
            self._active = False

    def debug_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paper_out": self._paper_out, "cover_open": self._cover_open,
                "overheated": self._overheated, "ribbon_fault": self._ribbon_fault,
                "cutter_pressed": self._cutter_pressed, "received_results": self._received,
                "expected_results": self._expected,
                "firmware": self._firmware, "battery": self._battery,
                "battery_low": self._battery_low, "charging": self._charging,
                "shutdown_value": self._shutdown_value,
                "serial_prefix": self._capabilities.serial_number[:4].decode("ascii", errors="replace") or None,
                "chip_type": self._capabilities.chip_type,
                "reported_supports_gray": self._capabilities.reported_supports_gray,
                "reported_gray_levels": self._capabilities.reported_gray_levels,
                "double_dpi": self._capabilities.double_dpi,
                "charging_print_restricted": self._capabilities.charging_print_restricted,
                "multiple_densities": self._capabilities.multiple_densities,
                "label_workshop": self._capabilities.label_workshop,
                "paper_sensor_suppression": self._capabilities.paper_sensor_suppression,
            }
