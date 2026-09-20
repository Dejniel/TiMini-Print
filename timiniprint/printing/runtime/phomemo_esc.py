from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ...protocol import ProtocolJob
from ...protocol.families.phomemo_esc.replies import PrintMasterReplyDecoder
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from .base import RuntimeController, RuntimeSessionApi

_COMPLETION_TIMEOUT_SEC = 180.0
_FAULTS = {
    (0x03, 0xA9): (PrinterStatusCode.OVERHEATED, "print-head overheat"),
    (0x05, 0x99): (PrinterStatusCode.COVER_OPEN, "an open cover"),
    (0x06, 0x88): (PrinterStatusCode.PAPER_OUT, "that it is out of paper"),
    (0x0B, 0xB8): (PrinterStatusCode.NOT_READY, "cancellation of the print job"),
}


@dataclass
class _Completion:
    finished: bool = False
    error: Exception | None = None


class PhomemoEscRuntimeController(RuntimeController):
    """Print Master status lifecycle, shared by raw and compressed recipes."""

    def __init__(self) -> None:
        self._decoder = PrintMasterReplyDecoder()
        self._lock = threading.RLock()
        self._completion: _Completion | None = None
        self._active = False
        self._ignore_pending_frame = False

    @asynccontextmanager
    async def job_scope(
        self, session: RuntimeSessionApi, job: ProtocolJob, *, timeout: float,
    ) -> AsyncIterator[None]:
        with self._lock:
            if self._active:
                raise RuntimeError("Print Master already has an active print job")
            self._active = True
            self._completion = _Completion() if job.wait_for_completion and session.can_wait_for_reply() else None
            # Finish decoding an older reply, but it cannot finish this job.
            self._ignore_pending_frame = self._decoder.has_pending_frame
        try:
            yield
        finally:
            with self._lock:
                self._completion = None
                self._active = False

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        with self._lock:
            frames = self._decoder.feed(payload)
            self._record_frames(session, frames)
            for frame in frames:
                if self._ignore_pending_frame:
                    self._ignore_pending_frame = False
                    continue
                self._record_status(session, frame)

    def _record_frames(self, session: RuntimeSessionApi, frames: list[bytes]) -> None:
        """Consume auxiliary replies; called while holding the receive lock."""

    def _record_status(self, session: RuntimeSessionApi, frame: bytes) -> None:
        status = tuple(frame)
        fault = _FAULTS.get(status)
        complete = frame == b"\x0f\x0c"
        if not complete and fault is None:
            return
        error = None
        if fault is not None:
            reason, detail = fault
            error = PrinterNotReadyError(f"Print Master reported {detail}", reason)
        session.report_debug(f"Print Master status: {frame.hex()} " + (str(error) if error else "complete"))
        completion = self._completion
        if completion is not None and not completion.finished:
            completion.error = error
            completion.finished = True

    async def wait_for_completion(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        with self._lock:
            completion = self._completion
        if completion is None:
            session.report_warning(
                short="Print Master completion unavailable",
                detail="The payload was sent, but the connection cannot receive passive completion replies.",
            )
            return
        if not self._completion_finished(completion):
            observed = session.can_observe_replies()
            # Without an observer the wait contract supplies an accumulated reply.
            # With an observer, match only job state, never notification history.
            def match(data: bytes) -> bool:
                if observed:
                    return self._completion_finished(completion)
                return any(frame == b"\x0f\x0c" or tuple(frame) in _FAULTS
                           for frame in PrintMasterReplyDecoder().feed(data))

            reply = await session.wait_for_reply(
                "Print Master completion", match,
                timeout=max(timeout, _COMPLETION_TIMEOUT_SEC), required=False,
            )
            if not observed and reply:
                self.handle_notification(session, reply)
        with self._lock:
            if not completion.finished:
                raise RuntimeError("Print Master page completion timed out")
            if completion.error is not None:
                raise completion.error

    def _completion_finished(self, completion: _Completion) -> bool:
        with self._lock:
            return completion.finished

    async def stop(self, session: RuntimeSessionApi) -> None:
        with self._lock:
            if self._completion is not None and not self._completion.finished:
                self._completion.error = RuntimeError("Print Master connection closed before completion")
                self._completion.finished = True
            self._completion = None
            self._active = False
