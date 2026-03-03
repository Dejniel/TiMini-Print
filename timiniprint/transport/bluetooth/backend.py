from __future__ import annotations

import asyncio
import threading
import time
from typing import List, Optional, Tuple

from .adapters import _get_ble_adapter, _get_classic_adapter
from .constants import RFCOMM_CHANNELS, IS_WINDOWS
from .types import DeviceInfo, DeviceTransport, ScanFailure, SocketLike
from ... import reporting


class SppBackend:
    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._sock: Optional[SocketLike] = None
        self._lock = threading.Lock()
        self._connected = False
        self._channel: Optional[int] = None
        self._transport: Optional[DeviceTransport] = None
        self._reporter = reporter

    @staticmethod
    async def scan(timeout: float = 5.0) -> List[DeviceInfo]:
        devices, _failures = await SppBackend.scan_with_failures(timeout=timeout)
        return devices

    @staticmethod
    async def scan_with_failures(
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> Tuple[List[DeviceInfo], List[ScanFailure]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            _scan_blocking,
            timeout,
            include_classic,
            include_ble,
        )

    async def connect(
        self,
        device: DeviceInfo,
        pairing_hint: Optional[bool] = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._connect_attempts_blocking, [device], pairing_hint)

    async def connect_attempts(
        self,
        attempts: List[DeviceInfo],
        pairing_hint: Optional[bool] = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._connect_attempts_blocking, attempts, pairing_hint)

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._disconnect_blocking)

    async def write(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_blocking, data, chunk_size, interval_ms)

    def _connect_attempts_blocking(
        self,
        attempts: List[DeviceInfo],
        pairing_hint: Optional[bool],
    ) -> None:
        if self._connected:
            self._reporter.debug(short="Bluetooth", detail="Bluetooth connect skipped: already connected")
            return
        if not attempts:
            raise RuntimeError("Bluetooth connection failed (no transport attempts provided)")
        unique_attempts = _unique_attempts(attempts)
        self._reporter.debug(
            short="Bluetooth",
            detail=(
                "Bluetooth connect plan: "
                + ", ".join(
                    f"{item.transport.value}({item.address})"
                    for item in unique_attempts
                )
            ),
        )
        errors: List[Tuple[DeviceInfo, Exception]] = []

        for index, candidate in enumerate(unique_attempts):
            self._reporter.debug(
                short="Bluetooth",
                detail=(
                    f"Bluetooth attempt {index + 1}/{len(unique_attempts)}: "
                    f"transport={candidate.transport.value} address={candidate.address}"
                ),
            )
            try:
                self._connect_with_device(candidate, pairing_hint)
                self._reporter.debug(
                    short="Bluetooth",
                    detail=(
                        f"Bluetooth attempt {index + 1} succeeded: "
                        f"transport={candidate.transport.value} address={candidate.address}"
                    ),
                )
                return
            except Exception as exc:
                errors.append((candidate, exc))
                self._reporter.debug(
                    short="Bluetooth",
                    detail=(
                        f"Bluetooth attempt {index + 1} failed: "
                        f"transport={candidate.transport.value} address={candidate.address} error={exc}"
                    ),
                )
                if index < len(unique_attempts) - 1:
                    next_transport = unique_attempts[index + 1].transport
                    self._reporter.warning(
                        detail=(
                            f"{_transport_label(candidate.transport)} Bluetooth connection failed, "
                            f"retrying over {_transport_label(next_transport)} "
                            f"(device: {candidate.name or candidate.address})."
                        ),
                    )

        if not errors:
            raise RuntimeError("Bluetooth connection failed")
        if len(errors) == 1:
            raise errors[0][1]

        parts = []
        for index, (attempt, error) in enumerate(errors):
            suffix = "fallback error" if index else "error"
            parts.append(f"{attempt.transport.value} {suffix}: {error}")
        detail = "; ".join(parts)
        raise RuntimeError(f"Bluetooth connection failed ({detail})")

    def _connect_with_device(self, device: DeviceInfo, pairing_hint: Optional[bool]) -> None:
        self._reporter.debug(
            short="Bluetooth",
            detail=(
                f"Connecting using {device.transport.value}: "
                f"address={device.address} pairing_hint={pairing_hint}"
            ),
        )
        adapter = _select_adapter(device.transport)
        if adapter is None:
            raise RuntimeError(f"{device.transport.value} Bluetooth is not supported on this platform")
        pair_error = None
        try:
            if pairing_hint and IS_WINDOWS:
                self._reporter.status(reporting.STATUS_PAIRING_CONFIRM)
            adapter.ensure_paired(device.address, pairing_hint)
            self._reporter.debug(short="Bluetooth", detail=f"Pairing check done for {device.address}")
        except Exception as exc:
            pair_error = exc
            self._reporter.debug(short="Bluetooth", detail=f"Pairing check failed for {device.address}: {exc}")
        channels = _resolve_rfcomm_channels(adapter, device.address)
        self._reporter.debug(
            short="Bluetooth",
            detail=f"RFCOMM channels for {device.transport.value} {device.address}: {channels}",
        )
        last_error = None
        for channel in channels:
            sock = None
            try:
                self._reporter.debug(
                    short="Bluetooth",
                    detail=f"Trying RFCOMM channel {channel} for {device.address}",
                )
                sock = adapter.create_socket(pairing_hint, reporter=self._reporter)
                set_timeout = getattr(sock, "settimeout", None)
                if callable(set_timeout):
                    set_timeout(8)
                sock.connect((device.address, channel))
                self._sock = sock
                self._connected = True
                self._channel = channel
                self._transport = device.transport
                self._reporter.debug(
                    short="Bluetooth",
                    detail=(
                        f"Connected via {device.transport.value} {device.address} "
                        f"on RFCOMM channel {channel}"
                    ),
                )
                return
            except Exception as exc:
                last_error = exc
                self._reporter.debug(
                    short="Bluetooth",
                    detail=f"RFCOMM channel {channel} failed for {device.address}: {exc}",
                )
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
        raise RuntimeError("Bluetooth connection failed (" + detail + ")")

    def _disconnect_blocking(self) -> None:
        if not self._sock:
            self._connected = False
            self._channel = None
            self._transport = None
            return
        try:
            self._sock.close()
        finally:
            self._sock = None
            self._connected = False
            self._channel = None
            self._transport = None

    def _write_blocking(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        if not self._sock or not self._connected:
            raise RuntimeError("Not connected to a Bluetooth device")
        interval = max(0.0, interval_ms / 1000.0)
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            with self._lock:
                _send_all(self._sock, chunk)
            offset += len(chunk)
            if interval:
                time.sleep(interval)


def _scan_blocking(
    timeout: float,
    include_classic: bool,
    include_ble: bool,
) -> Tuple[List[DeviceInfo], List[ScanFailure]]:
    classic_devices: List[DeviceInfo] = []
    ble_devices: List[DeviceInfo] = []
    failures: List[ScanFailure] = []
    classic_failure: Optional[Exception] = None
    ble_failure: Optional[Exception] = None
    attempts = 0
    if include_classic:
        attempts += 1
        adapter = _get_classic_adapter()
        if adapter is None:
            classic_failure = RuntimeError("Classic Bluetooth not supported")
        else:
            try:
                classic_devices = adapter.scan_blocking(timeout)
            except Exception as exc:
                classic_failure = exc
    if include_ble:
        attempts += 1
        adapter = _get_ble_adapter()
        if adapter is None:
            ble_failure = RuntimeError("BLE Bluetooth not supported")
        else:
            try:
                ble_devices = adapter.scan_blocking(timeout)
            except Exception as exc:
                ble_failure = exc

    if classic_failure:
        failures.append(ScanFailure(DeviceTransport.CLASSIC, classic_failure))
    if ble_failure:
        failures.append(ScanFailure(DeviceTransport.BLE, ble_failure))
    all_devices = classic_devices + ble_devices
    if all_devices:
        return DeviceInfo.dedupe(all_devices), failures
    if attempts and failures and len(failures) >= attempts:
        detail = "; ".join(f"{item.transport.value}: {item.error}" for item in failures)
        raise RuntimeError(f"Bluetooth scan failed ({detail})")
    return [], failures


def _select_adapter(transport: DeviceTransport):
    if transport == DeviceTransport.BLE:
        return _get_ble_adapter()
    return _get_classic_adapter()


def _unique_attempts(attempts: List[DeviceInfo]) -> List[DeviceInfo]:
    unique: List[DeviceInfo] = []
    seen = set()
    for device in attempts:
        key = (
            device.transport,
            (device.address or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(device)
    return unique


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


def _resolve_rfcomm_channels(adapter, address: str) -> List[int]:
    try:
        resolved = list(adapter.resolve_rfcomm_channels(address) or [])
    except Exception:
        resolved = []
    explicit_channels: List[int] = []
    for item in resolved:
        try:
            channel_id = int(item)
        except Exception:
            continue
        if channel_id > 0 and channel_id not in explicit_channels:
            explicit_channels.append(channel_id)
    if explicit_channels:
        return explicit_channels

    return [RFCOMM_CHANNELS[0]]


def _transport_label(transport: DeviceTransport) -> str:
    if transport == DeviceTransport.BLE:
        return "BLE"
    return "Classic"
