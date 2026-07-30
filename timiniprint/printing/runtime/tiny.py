from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ...protocol.families.tiny import (
    TINY_FLOW_PAUSE_PACKET,
    TINY_FLOW_RESUME_PACKET,
)
from .base import RuntimeController, RuntimeSessionApi


# Runtime control algorithms that select this controller. Devices opt in through
# runtime_overrides.control_algorithm in their printer config, so nothing changes
# for tiny printers that were never tested: one verified unit says nothing about
# the rest of the family, and a controller that waits for frames a device never
# sends would stall the job.
TINY_FLOW_CONTROL_ALGORITHMS = frozenset({"x5_poll"})


@dataclass
class _TinySessionState:
    pause_count: int = 0
    resume_count: int = 0


class TinyRuntimeController(RuntimeController):
    """Flow control for tiny-family printers that report buffer state.

    Standard writes are write-without-response, so the transport has no
    back-pressure of its own: it pushes a whole job out in seconds while the
    printer needs minutes, and whatever does not fit is dropped silently.

    The device does report when it cannot take more. Same shape as V5C and V5X:
    one pause frame, one resume frame, matched as complete packets.
    """

    _COMPLETION_QUIET_S: float = 3.0
    _COMPLETION_GRACE_S: float = 3.0
    _COMPLETION_MAX_S: float = 60.0

    def __init__(self) -> None:
        self._state = _TinySessionState()

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, TinyRuntimeController):
            self._state = previous._state

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "pause_count": self._state.pause_count,
            "resume_count": self._state.resume_count,
        }

    def debug_update(self, **changes: object) -> None:
        for key, value in changes.items():
            if not hasattr(self._state, key):
                raise KeyError(f"Unknown tiny debug field '{key}'")
            setattr(self._state, key, value)

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        if payload == TINY_FLOW_PAUSE_PACKET:
            self._state.pause_count += 1
            session.set_flow_paused(True, payload=payload)
            return
        if payload == TINY_FLOW_RESUME_PACKET:
            self._state.resume_count += 1
            session.set_flow_paused(False, payload=payload)
            return
        session.report_debug(f"tiny notify not recognised: {payload.hex()}")

    async def wait_for_completion(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        # Hold the BLE link after the job is sent until the printer finishes.
        # Same shape as the V5X controller: treat the job as done on the resume
        # frame (the device reporting it is free) or after a quiet window with no
        # frames, then wait a short grace before returning (caller disconnects).
        # We only listen -- v5x.py notes that polling 0xA3 to elicit status would
        # feed paper, and the tiny command set has no query known to be safe.
        _ = timeout
        if not session.can_wait_for_notification():
            return
        session.report_debug("tiny waiting for print completion before disconnect")
        deadline = time.monotonic() + self._COMPLETION_MAX_S
        capped = True
        while time.monotonic() < deadline:
            frame = await session.wait_for_notification(
                "tiny flow resume",
                lambda packet: packet == TINY_FLOW_RESUME_PACKET,
                timeout=self._COMPLETION_QUIET_S,
                required=False,
            )
            if frame is None:
                session.report_debug("tiny status quiet; print assumed finished")
                capped = False
                break
            session.report_debug("tiny reported free; print finished")
            capped = False
            break
        if capped:
            session.report_debug("tiny completion wait hit max cap; disconnecting anyway")
        if self._COMPLETION_GRACE_S > 0:
            await asyncio.sleep(self._COMPLETION_GRACE_S)
