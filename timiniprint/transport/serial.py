from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from .. import reporting
from ..devices import PrinterDevice, SerialTarget
from ..devices.profiles import StreamProfile
from ..protocol import ProtocolJob
from ._stream_receive import StreamReceiveHub
from .base import ConnectionCleanupError
from ._stream_session import StreamRuntimeSession, run_blocking

_READ_POLL_SEC = 0.05
_WRITE_TIMEOUT_SEC = 5.0


class _SerialReader:
    """Normalize pyserial's idle reads for the shared stream inbox."""

    def __init__(self, port) -> None:
        self._port = port

    def recv(self, size: int) -> bytes:
        first = self._port.read(1)
        if not first:
            raise TimeoutError()
        available = min(max(0, size - 1), self._port.in_waiting)
        return first + (self._port.read(available) if available else b"")


class SerialConnection:
    """One open serial session, shared by payloads, replies and runtime flow control.

    Obtain an opened connection through SerialConnector. The port stays open
    until disconnect(), including between protocol steps and print jobs.
    """

    def __init__(self, target: SerialTarget, *, stream: StreamProfile,
                 reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        if not isinstance(target, SerialTarget):
            raise ValueError("SerialConnection requires a SerialTarget")
        self._target = target
        self._stream = stream
        self._reporter = reporter
        self._port = None
        self._hub: StreamReceiveHub | None = None
        self._controller = None
        self._data_observer: Callable[[bytes], None] | None = None
        self._runtime_session = StreamRuntimeSession(reporter, label="Serial")
        self._write_lock = threading.Lock()
        self._closing = False

    @property
    def reporter(self) -> reporting.Reporter:
        return self._reporter

    @property
    def active_ble_profile(self) -> None:
        return None

    def can_send_control_packet(self) -> bool:
        return self._port is not None and not self._closing

    def can_send_standard_payload(self) -> bool:
        return self.can_send_control_packet()

    def can_query_control_packet(self) -> bool:
        return self.can_send_control_packet()

    def can_wait_for_reply(self) -> bool:
        return self.can_send_control_packet()

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0) -> None:
        if runtime_controller is not None and self._data_observer is not None:
            raise RuntimeError("Serial observer and printer runtime cannot share receive ownership")
        if runtime_controller is self._controller:
            return
        previous, self._controller = self._controller, None
        self._runtime_session.detach()
        if previous is not None:
            await previous.stop(self._runtime_session)
        if runtime_controller is not None:
            self._runtime_session.bind(
                asyncio.get_running_loop(), self.send_control_packet, self.can_send_control_packet,
            )
        self._controller = runtime_controller

    def _handle_payload(self, payload: bytes) -> None:
        controller = self._controller
        if controller is not None:
            self._runtime_session.dispatch(lambda: self._controller, payload)

    async def open(self, *, receive: bool = True, timeout: float | None = None) -> None:
        """Open native I/O once; deferred reception needs an observer/query to start.

        A setup deadline never abandons an uncancellable native open. The caller
        retains this connection and must disconnect after a failed attempt.
        """
        if self._port is not None or self._closing:
            raise RuntimeError("Serial connection cannot be reopened")
        operation = run_blocking(self._open, receive)
        if timeout is None:
            await operation
        else:
            try:
                await asyncio.wait_for(operation, timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError("Timed out opening serial port") from None

    def _open(self, receive: bool) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required. Install with: pip install -r requirements.txt") from exc
        self._port = serial.Serial(self._target.path, self._target.baud_rate,
                                   timeout=_READ_POLL_SEC, write_timeout=_WRITE_TIMEOUT_SEC)
        self._hub = StreamReceiveHub(
            _SerialReader(self._port), listener=self._handle_payload, on_error=self._runtime_session.abort,
        )
        if receive:
            self._hub.start()

    async def set_data_observer(self, observer: Callable[[bytes], None] | None) -> None:
        """Tap the one stream receiver, exclusively of a printer runtime."""
        self._require_open()
        if observer is not None and self._controller is not None:
            raise RuntimeError("Serial runtime already owns reception")
        self._data_observer = observer
        await run_blocking(self._hub.set_listener, observer if observer is not None else self._handle_payload)
        if observer is not None:
            await run_blocking(self._hub.start)

    async def quiesce(self) -> None:
        """Stop receive/wait scheduling; disconnect still owns native disposal."""
        if self._controller is not None:
            raise RuntimeError("A printer runtime owns this serial connection")
        self._closing = True
        self._runtime_session.close_flow()
        await run_blocking(self._stop_receive, cleanup=True)

    def _stop_receive(self) -> None:
        if self._hub is not None:
            self._hub.stop()
        self._data_observer = None

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        await run_blocking(self._write_packet, packet, timeout)
        return True

    def _write_packet(self, packet: bytes, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, min(timeout, _WRITE_TIMEOUT_SEC))
        if not self._write_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TimeoutError("Timed out waiting to write serial control packet")
        try:
            self._require_open()
            self._write_all(packet, deadline)
            self._drain_output(deadline)
        finally:
            self._write_lock.release()

    async def query_control_packet(
        self, packet: bytes, *, timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        return await run_blocking(self._query, packet, timeout, reply_complete)

    def _query(self, packet: bytes, timeout: float, reply_complete) -> bytes | None:
        hub = self._hub
        self._require_open()
        return hub.query(packet, self._write_packet, timeout=timeout, reply_complete=reply_complete)

    async def wait_for_reply(
        self, label: str, match: Callable[[bytes], bool], *, timeout: float, required: bool = True,
    ) -> bytes | None:
        return await run_blocking(self._wait, label, match, timeout, required)

    def _wait(self, label, match, timeout: float, required: bool) -> bytes | None:
        hub = self._hub
        self._require_open()
        return hub.wait_for_reply(label, match, timeout=timeout, required=required)

    async def send(self, job: ProtocolJob) -> None:
        if job.steps:
            raise RuntimeError("Protocol jobs with execution steps must be sent through ConnectedPrinter.send_job()")
        await self.send_standard_payload(job.payload)

    async def send_standard_payload(self, data: bytes) -> None:
        await run_blocking(self._write_payload, data)

    def _require_open(self) -> None:
        self._runtime_session.check_error()
        if not self.can_send_control_packet():
            raise RuntimeError("Serial connection is closed")

    def _write_all(self, data: bytes, deadline: float) -> None:
        previous = self._port.write_timeout
        try:
            offset = 0
            while offset < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out writing serial output")
                self._port.write_timeout = remaining
                written = self._port.write(data[offset:])
                if not written:
                    raise OSError("Serial write made no progress")
                offset += written
        finally:
            self._port.write_timeout = previous

    def _write_payload(self, data: bytes) -> None:
        self._require_open()
        offset = 0
        while offset < len(data):
            self._runtime_session.wait_for_flow()
            with self._write_lock:
                self._require_open()
                chunk = data[offset:offset + self._stream.chunk_size]
                self._write_all(chunk, time.monotonic() + _WRITE_TIMEOUT_SEC)
            offset += len(chunk)
            if self._stream.delay_ms:
                time.sleep(self._stream.delay_ms / 1000.0)
        with self._write_lock:
            self._require_open()
            self._drain_output(time.monotonic() + _WRITE_TIMEOUT_SEC)

    def _drain_output(self, deadline: float) -> None:
        # pyserial.flush() can wait forever, independently of write_timeout.
        # Drain with a deadline so context exit cannot discard queued bytes.
        while self._port.out_waiting:
            self._runtime_session.check_error()
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out draining serial output")
            time.sleep(0.01)

    async def disconnect(self) -> None:
        self._closing = True
        self._runtime_session.close_flow()
        previous, self._controller = self._controller, None
        self._runtime_session.detach()
        try:
            if previous is not None:
                await previous.stop(self._runtime_session)
        finally:
            await run_blocking(self._close, cleanup=True)

    def _close(self) -> None:
        self._stop_receive()
        self._hub = None
        with self._write_lock:
            if self._port is not None:
                self._port.close()
                self._port = None


class SerialConnector:
    """Open a bidirectional serial session for a device with SerialTarget."""

    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter

    async def connect(self, device: PrinterDevice) -> SerialConnection:
        connection = SerialConnection(device.transport_target, stream=device.profile.stream, reporter=self._reporter)
        try:
            await connection.open()
        except BaseException as error:
            try:
                await connection.disconnect()
            except (Exception, asyncio.CancelledError) as cleanup_error:
                raise error from ConnectionCleanupError(cleanup_error)
            raise
        return connection
