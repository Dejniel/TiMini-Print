"""Passive ToPrint status handling, without invented queries or completion ACKs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import RLock
from typing import TYPE_CHECKING

from ...protocol.families.toprint_control import ToPrintReplyDecoder, ToPrintStatus
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from ..step_execution import execute_protocol_step
from .base import RuntimeController, RuntimeSessionApi

if TYPE_CHECKING:
    from ...protocol import ProtocolJob, ProtocolStep


class ToPrintRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._lock = RLock()
        self._decoder = ToPrintReplyDecoder()
        self._status: ToPrintStatus | None = None
        self._mode: bytes | None = None
        self._job_fault: tuple[str, ...] | None = None

    @asynccontextmanager
    async def job_scope(
        self, session: RuntimeSessionApi, job: ProtocolJob, *, timeout: float,
    ) -> AsyncIterator[None]:
        with self._lock:
            self._raise_fault()
            self._job_fault = ()
        try:
            yield
            self._raise_fault()
        finally:
            with self._lock:
                self._job_fault = None

    async def before_write(self, session: RuntimeSessionApi, *, size: int, timeout: float) -> None:
        self._raise_fault()

    async def send_protocol_steps(
        self, session: RuntimeSessionApi, steps: tuple[ProtocolStep, ...], *, timeout: float,
    ) -> bool:
        if not session.can_send_standard_payload():
            return False
        for step in steps:
            self._raise_fault()
            await execute_protocol_step(session, step, timeout=timeout)
        return True

    def _raise_fault(self) -> None:
        with self._lock:
            errors = self._job_fault or (self._status.errors if self._status else ())
            if errors:
                raise PrinterNotReadyError(
                    f"ToPrint: {', '.join(errors)}", *(PrinterStatusCode(error) for error in errors),
                )

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        with self._lock:
            for reply in self._decoder.feed(payload):
                if isinstance(reply, bytes):
                    self._mode = reply
                    session.report_debug(f"ToPrint mode: {reply.hex()}")
                    continue
                self._status = reply
                if reply.errors and self._job_fault == ():
                    self._job_fault = reply.errors
                session.report_debug(
                    f"ToPrint status=0x{reply.raw:02x} printing={reply.printing} "
                    f"paused={reply.paused} errors={','.join(reply.errors) or '<none>'}"
                )

    def debug_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self._status.raw if self._status else None,
                "mode": self._mode.hex() if self._mode else None,
                "errors": list(self._status.errors) if self._status else [],
            }
