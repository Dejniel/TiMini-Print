"""Bounded diagnostics for selected runtime replies, not transport byte dumps."""
from __future__ import annotations

from dataclasses import dataclass
import time

from ... import reporting

_REPEAT_INTERVAL_SEC = 5.0
_MAX_LABELS = 32


@dataclass
class _Sample:
    fingerprint: int
    logged_at: float
    suppressed: int = 0


class RuntimeRxLog:
    """Per-controller RX sampling; never suppress delivery or state updates.

    Status changes are immediate. Identical statuses and parser diagnostics are
    summarized at most once per five seconds. Flow transitions are never sampled.
    Callers select control replies, excluding raster echoes and credential data.
    """

    def __init__(self) -> None:
        self._samples: dict[str, _Sample] = {}
        self._pause_started: float | None = None

    def record(self, session, label: str, payload: bytes, *, changes: bool = True) -> bool:
        payload = bytes(payload)
        now = time.perf_counter()
        fingerprint = hash(payload)
        previous = self._samples.get(label)
        if previous is not None:
            if (not changes or previous.fingerprint == fingerprint) and now - previous.logged_at < _REPEAT_INTERVAL_SEC:
                previous.suppressed += 1
                return False
        elif len(self._samples) >= _MAX_LABELS:
            self._samples.pop(next(iter(self._samples)))
        suppressed = previous.suppressed if previous else 0
        self._samples[label] = _Sample(fingerprint, now)
        self._emit(session, label, payload, f" suppressed={suppressed}" if suppressed else "")
        return True

    def flow(self, session, paused: bool, payload: bytes) -> None:
        payload = bytes(payload)
        now = time.perf_counter()
        elapsed = ""
        if paused:
            if self._pause_started is None:
                self._pause_started = now
        elif self._pause_started is not None:
            elapsed = f" paused_ms={int((now - self._pause_started) * 1000)}"
            self._pause_started = None
        if payload:
            self._emit(session, "Flow control " + ("pause" if paused else "resume"), payload, elapsed)

    @staticmethod
    def _emit(session, label: str, payload: bytes, suffix: str) -> None:
        log_filter = getattr(session, "protocol_log_filter", None)
        preview = log_filter.rx_preview(payload) if log_filter else reporting.rx_preview(payload)
        session.report_debug(f"{label}: rx_bytes={len(payload)} rx={preview}{suffix}")
