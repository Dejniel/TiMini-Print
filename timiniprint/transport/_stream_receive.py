from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import selectors
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


class StreamReceiveHub:
    """One bounded reply inbox, fed by a socket reader or native callbacks.

    Callback sources implement start_receive(data, failed) / stop_receive().
    Other sources expose recv(); non-selectable sources must bound each read
    and raise TimeoutError when idle (as the serial adapter does).
    """

    def __init__(
        self,
        sock: Any,
        *,
        listener: Callable[[bytes], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        max_history_bytes: int = 65536,
        poll_timeout: float = 0.1,
    ) -> None:
        self._sock = sock
        self._listener = listener
        self._on_error = on_error
        self._max_history_bytes = max(1024, int(max_history_bytes))
        self._poll_timeout = max(0.01, float(poll_timeout))
        self._inbox_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._history = bytearray()
        self._base_offset = 0
        self._passive_offset = 0
        self._waiters: list[_ReplyWaiter] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._native_started = False
        self._error: Exception | None = None

    @staticmethod
    def supports(source: Any) -> bool:
        callbacks = (callable(getattr(source, "start_receive", None))
                     and callable(getattr(source, "stop_receive", None)))
        return callbacks or callable(getattr(source, "recv", None))

    def set_listener(self, listener: Callable[[bytes], None] | None) -> None:
        with self._inbox_lock:
            self._listener = listener

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        with self._inbox_lock:
            if self._thread is not None or self._native_started or self._stop_event.is_set():
                return
            native = getattr(self._sock, "start_receive", None)
            if callable(native):
                self._native_started = True
            elif not callable(getattr(self._sock, "recv", None)):
                return
        # Native startup can synchronously deliver bytes or wait on its owner
        # loop, so never hold the inbox lock across this call.
        if callable(native):
            try:
                native(self.feed, self.fail)
            except BaseException:
                self._sock.stop_receive()
                self._native_started = False
                raise
            return
        with self._inbox_lock:
            # Poll only the reader: changing the shared socket timeout would
            # also shorten sendall() when the printer's buffer is full.
            selector = selectors.DefaultSelector() if callable(getattr(self._sock, "fileno", None)) else None
            try:
                if selector is not None:
                    selector.register(self._sock, selectors.EVENT_READ)
                self._thread = threading.Thread(
                    target=self._read_loop,
                    args=(selector,),
                    name="timiniprint-stream-receive",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                if selector is not None:
                    selector.close()
                self._thread = None
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop()

    def _stop(self) -> None:
        self._stop_event.set()
        with self._inbox_lock:
            for waiter in self._waiters:
                waiter.event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.2, self._poll_timeout * 2))
            if thread.is_alive():
                raise RuntimeError("Stream receive thread did not stop")
        if self._native_started:
            self._sock.stop_receive()
            self._native_started = False

    def mark(self) -> int:
        with self._inbox_lock:
            self._raise_error()
            return self._end_offset()

    def query(
        self, packet: bytes, send: Callable[[bytes, float], object], *,
        timeout: float, reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        """Serialize active transactions, not passive completion observers.

        Register before sending, and share one budget between waiting for a
        preceding transaction, native writes and collecting the response.
        The sender receives the remaining native-write budget. An already
        running native call without cancellation support remains owned until
        it returns; exceeding the budget must not detach live I/O.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._query_lock.acquire(timeout=max(0.0, timeout)):
            raise TimeoutError("Timed out waiting for another stream query")
        try:
            self.check_error()
            if self._stop_event.is_set():
                raise ConnectionError("Stream receiver is closed")
            waiter = self.register(self.mark(), reply_complete)
            try:
                self.start()
                self.check_error()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Stream query expired before sending")
                send(packet, remaining)
                return self.wait(waiter, timeout=max(0.0, deadline - time.monotonic()))
            finally:
                self.unregister(waiter)
        finally:
            self._query_lock.release()

    def wait_for_reply(
        self, label: str, match: Callable[[bytes], bool], *, timeout: float, required: bool,
    ) -> bytes | None:
        """Observe unsolicited bytes without reserving the active-query slot."""
        waiter = self.register_passive(match)
        try:
            self.start()
            reply = self.wait(waiter, timeout=timeout, claim_passive=True)
            if required and waiter.result is None:
                raise TimeoutError(f"Timed out waiting for protocol reply: {label}")
            return reply
        finally:
            self.unregister(waiter)

    def check_error(self) -> None:
        with self._inbox_lock:
            self._raise_error()

    def unregister(self, waiter: _ReplyWaiter) -> None:
        with self._inbox_lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def register(
        self,
        start_offset: int,
        match: Callable[[bytes], bool] | None,
    ) -> _ReplyWaiter:
        waiter = _ReplyWaiter(start_offset=max(0, int(start_offset)), match=match)
        with self._inbox_lock:
            self._raise_error()
            self._match_waiter(waiter)
            if self._stop_event.is_set():
                waiter.event.set()
            self._waiters.append(waiter)
        return waiter

    def register_passive(self, match: Callable[[bytes], bool]) -> _ReplyWaiter:
        with self._inbox_lock:
            self._raise_error()
            waiter = _ReplyWaiter(
                start_offset=max(self._base_offset, self._passive_offset),
                match=match,
            )
            self._match_waiter(waiter)
            if self._stop_event.is_set():
                waiter.event.set()
            self._waiters.append(waiter)
            return waiter

    def wait(
        self,
        waiter: _ReplyWaiter,
        *,
        timeout: float,
        claim_passive: bool = False,
    ) -> bytes | None:
        waiter.event.wait(timeout=max(0.0, timeout))
        with self._inbox_lock:
            if waiter.result is not None:
                result = waiter.result
            else:
                result = self._slice_from(waiter.start_offset)
                if not result:
                    result = None
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            if claim_passive:
                end = (
                    waiter.result_end_offset
                    if waiter.result_end_offset is not None
                    else self._end_offset()
                )
                self._passive_offset = max(self._passive_offset, end)
            if waiter.result is None:
                self._raise_error()
            return result

    def feed(self, payload: bytes) -> None:
        """Consume a native fragment before waking any matching transaction."""
        if self._stop_event.is_set() or not payload:
            return
        data = bytes(payload)
        try:
            with self._inbox_lock:
                if self._error is not None:
                    return
                listener = self._listener
            if listener is not None:
                listener(data)
            with self._inbox_lock:
                if self._stop_event.is_set() or self._error is not None:
                    return
                self._history.extend(data)
                self._trim_history()
                for waiter in tuple(self._waiters):
                    self._match_waiter(waiter)
        except Exception as exc:
            self.fail(exc)

    def fail(self, error: Exception) -> None:
        with self._inbox_lock:
            first = not self._stop_event.is_set() and self._error is None
            if first:
                self._error = error
            for waiter in self._waiters:
                waiter.event.set()
        if first and self._on_error is not None:
            self._on_error(error)

    def _raise_error(self) -> None:
        if self._error is not None:
            raise self._error

    def _read_loop(self, selector: selectors.BaseSelector | None) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    if selector is not None and not selector.select(timeout=self._poll_timeout):
                        continue
                    payload = self._sock.recv(4096)
                except Exception as exc:
                    if _is_timeout_error(exc):
                        self._stop_event.wait(0.005)
                        continue
                    self.fail(exc)
                    break
                if not payload:
                    self.fail(ConnectionError("Stream peer closed its input"))
                    break
                self.feed(payload)
        finally:
            if selector is not None:
                selector.close()
            with self._inbox_lock:
                for waiter in self._waiters:
                    waiter.event.set()

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


__all__ = ["StreamReceiveHub"]
