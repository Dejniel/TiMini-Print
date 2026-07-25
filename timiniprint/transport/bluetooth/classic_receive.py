from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time
from typing import Any


@dataclass
class _ReplyWaiter:
    start_offset: int
    match: Callable[[bytes], bool] | None
    event: threading.Event = field(default_factory=threading.Event)
    result: bytes | None = None
    result_end_offset: int | None = None


class ClassicReceiveHub:
    """Single-reader byte inbox for one connected Classic Bluetooth socket."""

    def __init__(
        self,
        sock: Any,
        *,
        listener: Callable[[bytes], None] | None = None,
        max_history_bytes: int = 65536,
        poll_timeout: float = 0.1,
    ) -> None:
        self._sock = sock
        self._listener = listener
        self._max_history_bytes = max(1024, int(max_history_bytes))
        self._poll_timeout = max(0.01, float(poll_timeout))
        self._condition = threading.Condition()
        self._history = bytearray()
        self._base_offset = 0
        self._passive_offset = 0
        self._waiters: list[_ReplyWaiter] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_timeout = None

    def set_listener(self, listener: Callable[[bytes], None] | None) -> None:
        with self._condition:
            self._listener = listener

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            gettimeout = getattr(self._sock, "gettimeout", None)
            if callable(gettimeout):
                try:
                    self._previous_timeout = gettimeout()
                except Exception:
                    self._previous_timeout = None
            settimeout = getattr(self._sock, "settimeout", None)
            if callable(settimeout):
                settimeout(self._poll_timeout)
            self._thread = threading.Thread(
                target=self._read_loop,
                name="timiniprint-classic-receive",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            for waiter in self._waiters:
                waiter.event.set()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.2, self._poll_timeout * 2))
        settimeout = getattr(self._sock, "settimeout", None)
        if callable(settimeout):
            try:
                settimeout(self._previous_timeout)
            except Exception:
                pass

    def mark(self) -> int:
        with self._condition:
            return self._end_offset()

    def register(
        self,
        start_offset: int,
        match: Callable[[bytes], bool] | None,
    ) -> _ReplyWaiter:
        waiter = _ReplyWaiter(start_offset=max(0, int(start_offset)), match=match)
        with self._condition:
            self._waiters.append(waiter)
            self._match_waiter(waiter)
        return waiter

    def register_passive(self, match: Callable[[bytes], bool]) -> _ReplyWaiter:
        with self._condition:
            waiter = _ReplyWaiter(
                start_offset=max(self._base_offset, self._passive_offset),
                match=match,
            )
            self._waiters.append(waiter)
            self._match_waiter(waiter)
            return waiter

    def wait(
        self,
        waiter: _ReplyWaiter,
        *,
        timeout: float,
        claim_passive: bool = False,
    ) -> bytes | None:
        waiter.event.wait(timeout=max(0.0, timeout))
        with self._condition:
            if waiter.result is not None:
                result = waiter.result
            else:
                result = self._slice_from(waiter.start_offset)
                if not result:
                    result = None
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            if claim_passive:
                self._passive_offset = (
                    waiter.result_end_offset
                    if waiter.result_end_offset is not None
                    else self._end_offset()
                )
            return result

    def _read_loop(self) -> None:
        recv = getattr(self._sock, "recv", None)
        if not callable(recv):
            return
        while not self._stop_event.is_set():
            try:
                payload = recv(4096)
            except Exception as exc:
                if _is_timeout_error(exc):
                    self._stop_event.wait(0.005)
                    continue
                break
            if not payload:
                break
            data = bytes(payload)
            with self._condition:
                self._history.extend(data)
                self._trim_history()
                for waiter in tuple(self._waiters):
                    self._match_waiter(waiter)
                listener = self._listener
                self._condition.notify_all()
            if listener is not None:
                listener(data)
        with self._condition:
            for waiter in self._waiters:
                waiter.event.set()
            self._condition.notify_all()

    def _match_waiter(self, waiter: _ReplyWaiter) -> None:
        if waiter.result is not None or waiter.match is None:
            return
        candidate = self._slice_from(waiter.start_offset)
        if candidate and waiter.match(candidate):
            waiter.result = candidate
            waiter.result_end_offset = max(self._base_offset, waiter.start_offset) + len(candidate)
            waiter.event.set()

    def _slice_from(self, start_offset: int) -> bytes:
        start = max(self._base_offset, start_offset) - self._base_offset
        return bytes(self._history[start:])

    def _trim_history(self) -> None:
        extra = len(self._history) - self._max_history_bytes
        if extra <= 0:
            return
        del self._history[:extra]
        self._base_offset += extra
        self._passive_offset = max(self._passive_offset, self._base_offset)

    def _end_offset(self) -> int:
        return self._base_offset + len(self._history)


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in {60, 110, 10060}:
            return True
        if getattr(exc, "winerror", None) in {60, 110, 10060}:
            return True
    return False


__all__ = ["ClassicReceiveHub"]
