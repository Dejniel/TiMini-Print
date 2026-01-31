from __future__ import annotations

import asyncio
import threading
import time
from typing import List, Optional

from .adapters import _get_adapter
from .constants import RFCOMM_CHANNELS, IS_WINDOWS
from .types import DeviceInfo, SocketLike
from ... import reporting


class SppBackend:
    def __init__(self, reporter: Optional[reporting.Reporter] = None) -> None:
        self._sock: Optional[SocketLike] = None
        self._lock = threading.Lock()
        self._connected = False
        self._channel: Optional[int] = None
        self._reporter = reporter

    @staticmethod
    async def scan(timeout: float = 5.0) -> List[DeviceInfo]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _scan_blocking, timeout)

    async def connect(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._connect_blocking, address, pairing_hint)

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._disconnect_blocking)

    async def write(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_blocking, data, chunk_size, interval_ms)

    def _connect_blocking(self, address: str, pairing_hint: Optional[bool]) -> None:
        if self._connected:
            return
        pair_error = None
        try:
            if pairing_hint and IS_WINDOWS:
                self._report_status(reporting.STATUS_PAIRING_CONFIRM)
            _get_adapter().ensure_paired(address)
        except Exception as exc:
            pair_error = exc
        channels = _resolve_rfcomm_channels(address)
        last_error = None
        for channel in channels:
            sock = None
            try:
                sock = _get_adapter().create_socket()
                set_timeout = getattr(sock, "settimeout", None)
                if callable(set_timeout):
                    set_timeout(8)
                sock.connect((address, channel))
                self._sock = sock
                self._connected = True
                self._channel = channel
                return
            except Exception as exc:
                last_error = exc
                _safe_close(sock)
        if last_error and _is_timeout_error(last_error):
            if pair_error:
                raise RuntimeError(
                    "Bluetooth connection timed out. Pairing attempt failed: "
                    f"{pair_error}. Tried RFCOMM channels: {channels}."
                )
            raise RuntimeError(
                "Bluetooth connection timed out. Make sure the printer is on, in range, and paired. "
                f"Tried RFCOMM channels: {channels}."
            )
        detail = f"channels tried: {channels}"
        if pair_error:
            detail += f", pairing failed: {pair_error}"
        if last_error:
            detail += f", last error: {last_error}"
        raise RuntimeError("Bluetooth SPP connection failed (" + detail + ")")

    def _report_status(self, key: str, **ctx) -> None:
        if self._reporter:
            self._reporter.status(key, **ctx)

    def _disconnect_blocking(self) -> None:
        if not self._sock:
            self._connected = False
            self._channel = None
            return
        try:
            self._sock.close()
        finally:
            self._sock = None
            self._connected = False
            self._channel = None

    def _write_blocking(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        if not self._sock or not self._connected:
            raise RuntimeError("Not connected to a Bluetooth SPP device")
        interval = max(0.0, interval_ms / 1000.0)
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            with self._lock:
                _send_all(self._sock, chunk)
            offset += len(chunk)
            if interval:
                time.sleep(interval)


def _scan_blocking(timeout: float) -> List[DeviceInfo]:
    return _get_adapter().scan_blocking(timeout)


def _safe_close(sock: Optional[SocketLike]) -> None:
    if not sock:
        return
    try:
        sock.close()
    except Exception:
        pass


def _send_all(sock: SocketLike, data: bytes) -> None:
    sendall = getattr(sock, "sendall", None)
    if callable(sendall):
        sendall(data)
        return
    send = getattr(sock, "send", None)
    if not callable(send):
        raise RuntimeError("Bluetooth socket does not support send")
    offset = 0
    while offset < len(data):
        sent = send(data[offset:])
        if not sent:
            raise RuntimeError("Bluetooth send failed")
        offset += sent


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in {60, 110, 10060}:
            return True
        winerror = getattr(exc, "winerror", None)
        if winerror in {60, 110, 10060}:
            return True
    return False


def _resolve_rfcomm_channels(address: str) -> List[int]:
    adapter = _get_adapter()
    channel = adapter.resolve_rfcomm_channel(address)
    if getattr(adapter, "single_channel", False):
        return [channel or RFCOMM_CHANNELS[0]]
    if channel is None:
        return list(RFCOMM_CHANNELS)
    channels = [channel]
    for candidate in RFCOMM_CHANNELS:
        if candidate != channel:
            channels.append(candidate)
    return channels
