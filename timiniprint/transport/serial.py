from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .. import reporting
from ..devices import PrinterDevice, SerialTarget
from ..protocol import ProtocolJob


class SerialConnection:
    """Serial connection that writes jobs using the device's stream settings."""

    def __init__(
        self,
        device: PrinterDevice,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> None:
        target = device.transport_target
        if not isinstance(target, SerialTarget):
            raise RuntimeError("SerialConnector requires a PrinterDevice with SerialTarget")
        self._device = device
        self._target = target
        self._reporter = reporter

    @property
    def reporter(self) -> reporting.Reporter:
        return self._reporter

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0) -> None:
        _ = runtime_controller, timeout
        return None

    def can_send_control_packet(self) -> bool:
        return False

    def can_query_control_packet(self) -> bool:
        return False

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        _ = packet, timeout
        return False

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        _ = packet, timeout, reply_complete
        return None

    async def send(self, job: ProtocolJob) -> None:
        """Send a stream-only protocol job over serial in blocking chunks."""
        if job.steps:
            raise RuntimeError(
                "Protocol jobs with execution steps must be sent through ConnectedPrinter.send_job()"
            )
        await self.send_standard_payload(job.payload)

    async def send_standard_payload(self, data: bytes) -> None:
        """Send raw protocol payload over serial in blocking chunks via an executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._write_blocking,
            data,
            self._device.profile.stream.chunk_size,
            self._device.profile.stream.delay_ms,
        )

    async def disconnect(self) -> None:
        """Serial writes are short-lived, so disconnect is a no-op."""
        return None

    def _write_blocking(self, data: bytes, chunk_size: int, delay_ms: int) -> None:
        try:
            import serial
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyserial is required. Install with: pip install -r requirements.txt") from exc
        delay = max(0.0, delay_ms / 1000.0)
        try:
            with serial.Serial(self._target.path, self._target.baud_rate, timeout=1, write_timeout=5) as ser:
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + chunk_size]
                    ser.write(chunk)
                    offset += len(chunk)
                    if delay:
                        time.sleep(delay)
                ser.flush()
        except Exception as exc:
            raise RuntimeError(f"Serial connection failed: {exc}") from exc


class SerialConnector:
    """Create serial connections for devices with ``SerialTarget``."""

    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter

    async def connect(self, device: PrinterDevice) -> SerialConnection:
        """Return a serial connection bound to the given device."""
        return SerialConnection(device, reporter=self._reporter)
