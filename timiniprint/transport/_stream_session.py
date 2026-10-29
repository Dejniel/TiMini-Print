"""Transport-side runtime callbacks shared by Classic and serial streams."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError

from .. import reporting

_NOTIFICATION_DISPATCH_TIMEOUT_SEC = 5.0


async def run_blocking(operation, *args, cleanup: bool = False):
    """Keep ownership of executor I/O through repeated caller cancellation.

    A failed cleanup must remain visible even when its caller was cancelled.
    Ordinary I/O retains cancellation as its primary outcome.
    """
    pending = asyncio.get_running_loop().run_in_executor(None, operation, *args)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not pending.cancelled():
            error = pending.exception()
            if cleanup and error is not None:
                raise error
        raise


class StreamRuntimeSession:
    """Notification callbacks and writer gating, with no printer semantics."""

    def __init__(self, reporter: reporting.Reporter, *, label: str, flow_resume_timeout_s: float = 600.0) -> None:
        self._reporter = reporter
        self._label = label
        self._flow_resume_timeout_s = max(0.0, float(flow_resume_timeout_s))
        self._flow_condition = threading.Condition()
        self._flow_paused = False
        self._flow_closed = False
        self._transport_error: Exception | None = None
        self._loop = None
        self._send_control = None
        self._can_send_control = None
        self._epoch = 0

    def bind(self, loop, send_control, can_send_control) -> None:
        """Bind notification-side reactions to their application event loop."""
        self._epoch += 1
        self._loop = loop
        self._send_control = send_control
        self._can_send_control = can_send_control

    def detach(self) -> None:
        self._epoch += 1
        self._loop = None
        self._send_control = None
        self._can_send_control = None

    def dispatch(self, current_controller, payload: bytes) -> None:
        """Update runtime state in its loop before reply predicates run."""
        loop, epoch = self._loop, self._epoch

        def notify():
            if epoch == self._epoch:
                controller = current_controller()
                if controller is not None:
                    controller.handle_notification(self, payload)

        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is None or current is loop:
            notify()
            return
        pending = Future()

        def deliver():
            if pending.cancelled():
                return
            try:
                notify()
            except BaseException as exc:
                if not pending.done():
                    pending.set_exception(exc)
            else:
                if not pending.done():
                    pending.set_result(None)

        loop.call_soon_threadsafe(deliver)
        try:
            pending.result(timeout=_NOTIFICATION_DISPATCH_TIMEOUT_SEC)
        except FutureTimeoutError:
            if pending.done():
                raise
            pending.cancel()
            raise TimeoutError("Application loop did not process a stream notification")

    def can_send_control_packet(self) -> bool:
        return (self._transport_error is None
                and self._can_send_control is not None and self._can_send_control())

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        if self._send_control is None:
            return False
        return await self._send_control(packet, timeout=timeout)

    def report_debug(self, message: str) -> None:
        self._reporter.debug(short="Runtime", detail=message)

    def report_trace(self, message: str) -> None:
        self._reporter.trace(short="Runtime", detail=message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self._reporter.warning(short=short, detail=detail)

    def report_status(self, message: str) -> None:
        self._reporter.status(short=message)

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        with self._flow_condition:
            if self._flow_closed or self._transport_error is not None:
                return
            changed = self._flow_paused != paused
            self._flow_paused = paused
            if not paused:
                self._flow_condition.notify_all()
        if changed:
            action = "pause" if paused else "resume"
            self.report_debug(f"{self._label} flow {action}")
            if payload:
                self.report_trace(f"{self._label} flow {action}: {payload.hex()}")

    def reopen_flow(self) -> None:
        with self._flow_condition:
            self._transport_error = None
            self._flow_paused = False
            self._flow_closed = False
            self._flow_condition.notify_all()

    def close_flow(self) -> None:
        with self._flow_condition:
            self._flow_paused = False
            self._flow_closed = True
            self._flow_condition.notify_all()

    def wait_for_flow(self) -> None:
        deadline = time.monotonic() + self._flow_resume_timeout_s
        with self._flow_condition:
            while self._flow_paused and not self._flow_closed and self._transport_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for {self._label} flow-control resume")
                self._flow_condition.wait(timeout=remaining)
            self.check_error()
            if self._flow_closed:
                raise RuntimeError(f"{self._label} disconnected while waiting for flow-control resume")

    def abort(self, error: Exception) -> None:
        """A failed receiver must wake writers which can no longer get resume."""
        with self._flow_condition:
            if self._transport_error is None:
                self._transport_error = error
            self._flow_condition.notify_all()

    def check_error(self) -> None:
        if self._transport_error is not None:
            raise self._transport_error
