from __future__ import annotations

import time

from .base import RuntimeSessionApi

_STATUS_PREFIX = 0x1A
_TEMPERATURE_STATUS = 0x03
_COVER_STATUS = 0x05
_PAPER_STATUS = 0x06
_COMPLETION_STATUS = 0x0F


async def wait_for_phomemo_completion(
    session: RuntimeSessionApi,
    *,
    timeout: float,
    device_label: str,
    maximum_wait: float = 180.0,
) -> None:
    if not session.can_wait_for_reply():
        session.report_warning(
            short=f"{device_label} completion unavailable",
            detail=(
                "The current connection cannot receive the printer's passive "
                "completion status. The payload was sent, but completion could "
                "not be confirmed."
            ),
        )
        return

    deadline = time.monotonic() + maximum_wait
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"{device_label} page completion timed out")
        reply = await session.wait_for_reply(
            f"{device_label} completion",
            contains_phomemo_status_frame,
            timeout=min(max(0.05, timeout), remaining),
            required=True,
        )
        if reply is None:
            continue
        for status_type, value in phomemo_status_frames(reply):
            outcome = phomemo_status_outcome(status_type, value)
            session.report_debug(
                f"{device_label} status: "
                f"type=0x{status_type:02X} value=0x{value:02X} outcome={outcome}"
            )
            if status_type == _TEMPERATURE_STATUS and value == 0xA9:
                raise RuntimeError(f"{device_label} reported print-head overheat")
            if status_type == _COVER_STATUS and value == 0x99:
                raise RuntimeError(f"{device_label} reported an open cover")
            if status_type == _PAPER_STATUS and value == 0x88:
                raise RuntimeError(f"{device_label} reported that it is out of paper")
            if status_type == _COMPLETION_STATUS:
                if value == 0x0C:
                    return
                raise RuntimeError(
                    f"{device_label} reported page failure 0x{value:02X}"
                )


def contains_phomemo_status_frame(payload: bytes) -> bool:
    return any(True for _status in phomemo_status_frames(payload))


def phomemo_status_frames(payload: bytes):
    for index in range(max(0, len(payload) - 2)):
        if payload[index] == _STATUS_PREFIX:
            yield payload[index + 1], payload[index + 2]


def phomemo_status_outcome(status_type: int, value: int) -> str:
    if status_type == _TEMPERATURE_STATUS:
        return "temperature-normal" if value == 0xA8 else "overheat"
    if status_type == _COVER_STATUS:
        return "cover-closed" if value == 0x98 else "cover-open"
    if status_type == _PAPER_STATUS:
        return "paper-out" if value == 0x88 else "paper-present"
    if status_type == _COMPLETION_STATUS:
        return "complete" if value == 0x0C else "failed"
    return "unknown"


__all__ = [
    "contains_phomemo_status_frame",
    "phomemo_status_frames",
    "phomemo_status_outcome",
    "wait_for_phomemo_completion",
]
