from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress

from ...protocol.families.niimbot.core import heartbeat_query, heartbeat_status_from_reply
from ..step_execution import execute_protocol_step, reply_matches_for
from .base import RuntimeSessionApi


_HEARTBEAT_INTERVAL_SEC = 2.0
_HEARTBEAT_TIMEOUT_SEC = 0.5
_LEGACY_RETRY_DELAY_SEC = 0.05


class NiimbotHeartbeat:
    """Idle queries on the prepared session, excluded from whole print sends."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock: asyncio.Lock | None = None
        self.missed_replies = 0
        self.status: dict[str, int] = {}

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self, session: RuntimeSessionApi, *, protocol_version: int | None,
        effective_model_id: int | None, job_active: Callable[[], bool], timeout: float,
        legacy: bool = False,
    ) -> None:
        await self.stop()
        if not session.can_observe_replies() or not (
            session.can_query_control_packet() or session.can_send_control_packet_wait_notification()
        ):
            return
        self._lock = asyncio.Lock()
        self.missed_replies = 0
        self.status = {}
        step = heartbeat_query(protocol_version=protocol_version, effective_model_id=effective_model_id)
        self._task = asyncio.create_task(self._run(
            session, step, job_active=job_active, timeout=min(timeout, _HEARTBEAT_TIMEOUT_SEC),
            effective_model_id=effective_model_id, attempts=5 if legacy else 1,
        ))

    @asynccontextmanager
    async def job_scope(self):
        # Let an in-flight heartbeat finish before arming print reply state.
        # Hold this across setup, raster transfer and completion, not chunks.
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            self.missed_replies = 0
            yield

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        if task.get_loop() is asyncio.get_running_loop():
            await self._cancel(task)
        else:
            # BLE adapters can own a different loop from session preparation.
            done = asyncio.run_coroutine_threadsafe(self._cancel(task), task.get_loop())
            await asyncio.wrap_future(done)

    @staticmethod
    async def _cancel(task: asyncio.Task) -> None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self, session, step, *, job_active, timeout, effective_model_id, attempts):
        while True:
            async with self._lock:
                # Continuation pages may leave a device-side job open even
                # while the application is not sending a page.
                if job_active():
                    self.missed_replies = 0
                else:
                    for attempt in range(attempts):
                        try:
                            reply = await execute_protocol_step(session, step, timeout=timeout)
                        except TimeoutError:
                            reply = None
                        except Exception as exc:
                            session.report_warning(short="NIIMBOT heartbeat stopped", detail=str(exc))
                            return
                        if reply_matches_for(step, reply):
                            self.status = heartbeat_status_from_reply(reply, effective_model_id=effective_model_id)
                            self.missed_replies = 0
                            break
                        if attempt + 1 < attempts:
                            await asyncio.sleep(_LEGACY_RETRY_DELAY_SEC)
                    else:
                        self.missed_replies += 1
                        if self.missed_replies == 1:
                            session.report_warning(
                                short="NIIMBOT heartbeat unavailable",
                                detail="No matching idle heartbeat reply; keeping the connection open.",
                            )
            # The interval follows the exchange; it is not a fixed-rate timer.
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SEC)
