from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ...devices import PrinterDevice
from ...protocol import ProtocolJob, ProtocolStep, ProtocolStepOperation
from ...protocol.families.yk_astra_p1.session import AstraP1Status, paper_details_from_frame, status_from_frame
from ...protocol.families.yk_common import YkFrame, iter_yk_frames, pack_yk_frame, resequence_yk_frame
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from .base import RuntimeSessionApi
from .yk_astra_p1 import AstraP1RuntimeController


class XEasyLabelRuntimeController(AstraP1RuntimeController):
    """Astra P1 sequencing and job-scoped completion for XEasyLabel recipes."""

    def __init__(self, *, paper_query_on_valid: bool = False, check_cutter: bool = False) -> None:
        super().__init__(paper_query_on_valid=paper_query_on_valid)
        self._astra_state.next_sequence = 1
        self._check_cutter = check_cutter
        self._job_active = False
        self._error: PrinterNotReadyError | None = None
        self._paper_query_needed = False

    @asynccontextmanager
    async def job_scope(
        self, session: RuntimeSessionApi, job: ProtocolJob, *, timeout: float,
    ) -> AsyncIterator[None]:
        self._reset_completion_tracking()
        self._job_active = True
        try:
            self._raise_if_not_ready()
            yield
            self._raise_if_not_ready()
        finally:
            self._job_active = False
            self._reset_completion_tracking()

    async def send_protocol_steps(
        self, session: RuntimeSessionApi, steps: tuple[ProtocolStep, ...], *, timeout: float,
    ) -> bool:
        if not session.can_send_standard_payload() or not steps:
            return False
        packets: list[bytes] = []
        for step in steps:
            if step.operation is not ProtocolStepOperation.SEND:
                return False
            frames = tuple(iter_yk_frames(step.data))
            if sum(frame.packet_length for frame in frames) != len(step.data):
                return False
            offset = 0
            for frame in frames:
                packets.append(step.data[offset:offset + frame.packet_length])
                offset += frame.packet_length
        if self._paper_query_needed and session.can_query_control_packet():
            await self._query_paper_details(session, timeout=timeout)
        for packet in packets:
            self._raise_if_not_ready()
            await session.send_standard_payload(resequence_yk_frame(packet, sequence=self._take_sequence()))
        self._raise_if_not_ready()
        return True

    def _consume_completed_job(self) -> bool:
        self._raise_if_not_ready()
        return super()._consume_completed_job()

    def diagnostic_fields(self, device: PrinterDevice) -> tuple[str, ...]:
        fields = ("status", "battery", "firmware", "serial")
        return fields + (("paper",) if self._astra_state.paper_query_on_valid else ())

    def debug_snapshot(self) -> dict[str, object]:
        return {
            **super().debug_snapshot(),
            "job_active": self._job_active,
            "paper_query_pending": self._paper_query_needed,
            "cutter_ready": None if self._astra_state.last_status is None else self._astra_state.last_status.cutter_ready,
        }

    async def query_diagnostic(
        self, device: PrinterDevice, session: RuntimeSessionApi, field: str, *, timeout: float,
    ) -> object | None:
        if field not in self.diagnostic_fields(device):
            raise ValueError(f"Unsupported XEasyLabel diagnostic field: {field}")
        if not session.can_query_control_packet():
            return None
        if field in ("status", "battery"):
            command, payload = 0x10, b""
        elif field == "paper":
            command, payload = 0x72, b"\x01"
        else:
            command, payload = (0x11 if field == "firmware" else 0x12), b""

        def matching(data: bytes | None) -> YkFrame | None:
            for frame in iter_yk_frames(data or b""):
                if command == 0x10 and status_from_frame(frame) is not None:
                    return frame
                if command == 0x72 and paper_details_from_frame(frame) is not None:
                    return frame
                if command == 0x11 and frame.command == 0xF1 and len(frame.payload) == 2:
                    return frame
                if command == 0x12 and frame.command == 0xF2 and frame.payload:
                    return frame
            return None

        try:
            reply = await session.query_control_packet(
                pack_yk_frame(command, payload, sequence=self._take_sequence()),
                timeout=timeout, reply_complete=lambda data: matching(data) is not None,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return None
        frame = matching(reply)
        if frame is None:
            return None
        self._consume_frames(session, iter_yk_frames(reply or b""))
        if field == "battery":
            return self._astra_state.last_status.battery_percent
        if field == "status":
            return self.debug_snapshot()
        if field == "paper":
            self._paper_query_needed = False
            return self.debug_snapshot()["paper_details"]
        if field == "firmware":
            return f"{frame.payload[1]}.{frame.payload[0]}"
        return frame.payload.decode("ascii", errors="replace").rstrip("\x00")

    def _consume_status(self, session: RuntimeSessionApi, status: AstraP1Status) -> None:
        previous = self._astra_state.last_status
        if self._astra_state.paper_query_on_valid:
            if not status.valid_paper:
                self._astra_state.paper_details = None
                self._paper_query_needed = False
            elif previous is None or not previous.valid_paper:
                self._paper_query_needed = True
        super()._consume_status(session, status)
        reasons = tuple(code for flag, code in (
            (status.cover_open, PrinterStatusCode.COVER_OPEN),
            (status.low_battery, PrinterStatusCode.LOW_BATTERY),
            (status.overheating, PrinterStatusCode.OVERHEATED),
            (status.paper_out, PrinterStatusCode.PAPER_OUT),
            (self._check_cutter and not status.cutter_ready, PrinterStatusCode.CUTTER_ERROR),
        ) if flag)
        error = (
            PrinterNotReadyError(
                f"XEasyLabel printer reports {', '.join(reason.value for reason in reasons)} "
                f"(status=0x{status.status_bits:04x})", *reasons,
            )
            if reasons else None
        )
        if not self._job_active or self._error is None:
            self._error = error
        if not self._job_active:
            self._reset_completion_tracking()

    def _status_completes_job(self, status: AstraP1Status, *, saw_printing: bool) -> bool:
        return self._job_active and (
            status.command == 0x81
            or super()._status_completes_job(status, saw_printing=saw_printing)
        )

    def _completion_reply(self, data: bytes) -> bool:
        return self._error is not None or super()._completion_reply(data)

    def _raise_if_not_ready(self) -> None:
        if self._error is not None:
            raise self._error

    async def _query_paper_details(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        self._paper_query_needed = False
        await super()._query_paper_details(session, timeout=timeout)
