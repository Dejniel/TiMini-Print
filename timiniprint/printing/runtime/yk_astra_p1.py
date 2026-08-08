from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...protocol.families.yk_astra_p1.session import (
    AstraP1PaperDetails,
    AstraP1Status,
    first_paper_details,
    first_status,
    pack_paper_query,
    pack_status_query,
    paper_details_from_frame,
    status_from_frame,
)
from ...protocol.families.yk_common import (
    YkFrame,
    YkFrameStreamDecoder,
    iter_yk_frames,
)
from .base import RuntimeController, RuntimeSessionApi

_COMPLETION_TIMEOUT_SEC = 30.0


@dataclass
class _AstraP1RuntimeState:
    paper_query_on_valid: bool
    decoder: YkFrameStreamDecoder = field(default_factory=YkFrameStreamDecoder)
    next_sequence: int = 0
    last_status: AstraP1Status | None = None
    paper_details: AstraP1PaperDetails | None = None
    saw_printing: bool = False
    finished: bool = False
    completion_count: int = 0
    status_probe_available: bool = True


class AstraP1RuntimeController(RuntimeController):
    def __init__(self, *, paper_query_on_valid: bool = False) -> None:
        self._astra_state = _AstraP1RuntimeState(
            paper_query_on_valid=paper_query_on_valid,
        )

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if not isinstance(previous, AstraP1RuntimeController):
            return
        if (
            previous._astra_state.paper_query_on_valid
            != self._astra_state.paper_query_on_valid
        ):
            return
        self._astra_state = previous._astra_state

    async def probe_capabilities(
        self,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> None:
        if not session.can_query_control_packet():
            self._astra_state.status_probe_available = False
            session.report_warning(
                short="YK Astra P1 status unavailable",
                detail=(
                    "The connection cannot query YK Astra P1 status. Printing can "
                    "continue, but printer errors and paper details are unavailable."
                ),
            )
            return
        reply = await session.query_control_packet(
            pack_status_query(sequence=self._take_sequence()),
            timeout=max(1.0, timeout),
            reply_complete=lambda data: first_status(data) is not None,
        )
        status = first_status(reply)
        if status is None:
            self._astra_state.status_probe_available = False
            session.report_warning(
                short="YK Astra P1 status timeout",
                detail=(
                    "The printer did not return a usable YK Astra P1 status. "
                    "Printing can continue without preflight diagnostics."
                ),
            )
            return
        self._astra_state.status_probe_available = True
        self._consume_status(session, status)
        if self._astra_state.paper_query_on_valid and status.valid_paper:
            await self._query_paper_details(session, timeout=timeout)

    async def wait_for_completion(
        self,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> None:
        if self._consume_completed_job():
            return
        if not session.can_wait_for_reply():
            session.report_warning(
                short="YK Astra P1 completion unavailable",
                detail=(
                    "The connection cannot passively receive YK Astra P1 status "
                    "replies; the image was sent, but printer-side completion is unverified."
                ),
            )
            self._reset_completion_tracking()
            return
        reply = await session.wait_for_reply(
            "YK Astra P1 print completion",
            self._completion_reply,
            timeout=max(timeout, _COMPLETION_TIMEOUT_SEC),
            required=False,
        )
        if reply:
            self._consume_frames(session, iter_yk_frames(reply))
        if not self._consume_completed_job():
            session.report_warning(
                short="YK Astra P1 completion timeout",
                detail=(
                    "No printing-to-idle YK Astra P1 status transition arrived "
                    "before timeout."
                ),
            )
            self._reset_completion_tracking()

    def handle_notification(
        self,
        session: RuntimeSessionApi,
        payload: bytes,
    ) -> None:
        self._consume_frames(session, self._astra_state.decoder.feed(payload))

    def debug_snapshot(self) -> dict[str, object]:
        status = self._astra_state.last_status
        paper = self._astra_state.paper_details
        return {
            "next_sequence": self._astra_state.next_sequence,
            "status_probe_available": self._astra_state.status_probe_available,
            "status_bits": None if status is None else status.status_bits,
            "is_printing": False if status is None else status.is_printing,
            "cover_open": False if status is None else status.cover_open,
            "low_battery": False if status is None else status.low_battery,
            "overheating": False if status is None else status.overheating,
            "paper_out": False if status is None else status.paper_out,
            "valid_paper": False if status is None else status.valid_paper,
            "paper_loaded": False if status is None else status.paper_loaded,
            "density": None if status is None else status.density,
            "auto_off_time": None if status is None else status.auto_off_time,
            "speed": None if status is None else status.speed,
            "battery_percent": None if status is None else status.battery_percent,
            "paper_details": None
            if paper is None
            else {
                "paper_type": paper.paper_type,
                "width": paper.width,
                "length": paper.length,
                "color": paper.color,
            },
            "saw_printing": self._astra_state.saw_printing,
            "finished": self._astra_state.finished,
            "completion_count": self._astra_state.completion_count,
            "pending_reply_bytes": len(self._astra_state.decoder.pending),
        }

    async def _query_paper_details(
        self,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> None:
        reply = await session.query_control_packet(
            pack_paper_query(sequence=self._take_sequence()),
            timeout=max(1.0, timeout),
            reply_complete=lambda data: first_paper_details(data) is not None,
        )
        details = first_paper_details(reply)
        if details is None:
            session.report_warning(
                short="YK Astra P1 paper query timeout",
                detail=(
                    "The printer reported valid paper but did not return paper details."
                ),
            )
            return
        self._astra_state.paper_details = details
        session.report_debug(
            "YK Astra P1 paper: "
            f"type={details.paper_type} width={details.width} "
            f"length={details.length} color={details.color}"
        )

    def _completion_reply(self, data: bytes) -> bool:
        saw_printing = self._astra_state.saw_printing
        for frame in iter_yk_frames(data):
            status = status_from_frame(frame)
            if status is None or not status.completion_status:
                continue
            if status.is_printing:
                saw_printing = True
            elif saw_printing:
                return True
        return False

    def _consume_frames(
        self,
        session: RuntimeSessionApi,
        frames: Iterable[YkFrame],
    ) -> None:
        for frame in frames:
            status = status_from_frame(frame)
            if status is not None:
                self._consume_status(session, status)
            details = paper_details_from_frame(frame)
            if details is not None:
                self._astra_state.paper_details = details

    def _consume_status(
        self,
        session: RuntimeSessionApi,
        status: AstraP1Status,
    ) -> None:
        previous = self._astra_state.last_status
        self._astra_state.last_status = status
        if status.completion_status:
            if status.is_printing:
                self._astra_state.saw_printing = True
            elif self._astra_state.saw_printing and not self._astra_state.finished:
                self._astra_state.finished = True
                self._astra_state.completion_count += 1
        if status.errors and (
            previous is None or previous.status_bits != status.status_bits
        ):
            session.report_warning(
                short="YK Astra P1 printer state",
                detail=(
                    f"Printer reports {', '.join(status.errors)} "
                    f"(status=0x{status.status_bits:04x})."
                ),
            )
        if previous is None or previous.status_bits != status.status_bits:
            session.report_debug(
                "YK Astra P1 status: "
                f"bits=0x{status.status_bits:04x} "
                f"printing={status.is_printing} battery={status.battery_percent}"
            )

    def _consume_completed_job(self) -> bool:
        if not self._astra_state.finished:
            return False
        self._reset_completion_tracking()
        return True

    def _reset_completion_tracking(self) -> None:
        self._astra_state.saw_printing = False
        self._astra_state.finished = False

    def _take_sequence(self) -> int:
        sequence = self._astra_state.next_sequence
        self._astra_state.next_sequence = (sequence + 1) % 64
        return sequence


__all__ = ["AstraP1RuntimeController"]
