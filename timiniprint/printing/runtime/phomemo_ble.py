"""Phomemo BLE write/burst allowances, independent of page completion."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable


class PhomemoBleWriteWindow:
    """One connection's additive credits and negotiated application payload limit.

    The native writer still owns its GATT maximum and host-buffer readiness.
    This window only grants one physical write, possibly shorter than that
    maximum. Failed or cancelled writes are not refunded or retransmitted.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()
        self._credits = 0
        self._payload_limit = 182
        self._writes_per_credit = 1
        self._write_count = 0
        self._chip_6951 = False
        self._reset_at: float | None = None
        self._stopped = False

    def wake(self) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._changed.set)

    def grant(self, count: int) -> None:
        with self._lock:
            if self._stopped:
                return
            self._credits += count
            self.wake()

    def set_payload_limit(self, size: int) -> None:
        with self._lock:
            if not self._stopped:
                self._payload_limit = size
                self.wake()

    def set_burst(self, count: int) -> None:
        with self._lock:
            if not self._stopped:
                self._chip_6951 = True
                self._writes_per_credit = 5 if count == 1 else max(1, count)
                self.wake()

    def received_serial(self) -> None:
        with self._lock:
            if not self._stopped and self._chip_6951:
                self._reset_at = time.monotonic() + 1.0
                self.wake()

    def _apply_reset(self, now: float) -> None:
        if self._reset_at is not None and now >= self._reset_at:
            self._credits = 1
            self._write_count = 0
            self._reset_at = None

    async def reserve(
        self, size: int, *, timeout: float, check_error: Callable[[], None],
        report_debug: Callable[[str], None],
    ) -> int:
        deadline = time.monotonic() + max(0.0, timeout)
        waiting = False
        while True:
            check_error()
            with self._lock:
                if self._stopped:
                    raise ConnectionError("Phomemo BLE connection closed while awaiting write credit")
                now = time.monotonic()
                self._apply_reset(now)
                if self._payload_limit <= 0:
                    raise ValueError("Phomemo BLE announced a zero write-payload limit")
                if self._credits > 0:
                    self._write_count += 1
                    if self._write_count >= self._writes_per_credit:
                        self._credits -= 1
                        self._write_count = 0
                    if waiting:
                        report_debug("Phomemo BLE write credits resumed")
                    return min(size, self._payload_limit)
                remaining = deadline - now
                if remaining <= 0:
                    raise TimeoutError("Phomemo BLE did not grant another write credit")
                if self._reset_at is not None:
                    remaining = min(remaining, max(0.0, self._reset_at - now))
                self._changed.clear()
            if not waiting:
                report_debug("Phomemo BLE waiting for write credits")
                waiting = True
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                # A delayed chip-window reset is not a protocol timeout.
                continue

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._credits = 0
            self._write_count = 0
            self._reset_at = None
            self.wake()

    def debug_snapshot(self) -> dict:
        with self._lock:
            self._apply_reset(time.monotonic())
            return {
                "ble_write_credits": self._credits,
                "ble_payload_limit": self._payload_limit,
                "ble_writes_per_credit": self._writes_per_credit,
                "ble_writes_in_credit": self._write_count,
                "ble_chip_6951": self._chip_6951,
                "ble_credit_reset_pending": self._reset_at is not None,
            }
