from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
RFCOMM_CHANNELS = [1, 2, 3, 4, 5]
SocketLike = Any


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    address: str


class _BluetoothAdapter:
    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        raise NotImplementedError

    def create_socket(self) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return None


class _BlueZAdapter(_BluetoothAdapter):
    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices = _scan_bluetoothctl(timeout)
        if devices:
            return devices
        return _scan_bleak(timeout)

    def create_socket(self) -> SocketLike:
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError(
                "RFCOMM sockets are not supported on this system. Use --serial or run on Linux."
            )
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return _resolve_rfcomm_channel_linux(address)


class _WindowsBluetoothAdapter(_BluetoothAdapter):
    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        return _scan_pybluez(timeout)

    def create_socket(self) -> SocketLike:
        if hasattr(socket, "AF_BTH") and hasattr(socket, "BTHPROTO_RFCOMM"):
            return socket.socket(socket.AF_BTH, socket.SOCK_STREAM, socket.BTHPROTO_RFCOMM)
        try:
            import bluetooth  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Windows Bluetooth RFCOMM sockets are not supported by this Python build. "
                "Install PyBluez or use a Python build with AF_BTH enabled."
            ) from exc
        try:
            return bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        except Exception as exc:
            raise RuntimeError(
                "Windows Bluetooth RFCOMM sockets are not supported by this Python build. "
                f"PyBluez socket creation failed: {exc}"
            ) from exc

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return _resolve_rfcomm_channel_windows(address)


_ADAPTER: Optional[_BluetoothAdapter] = None


def _get_adapter() -> _BluetoothAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        if sys.platform.startswith("win"):
            _ADAPTER = _WindowsBluetoothAdapter()
        else:
            _ADAPTER = _BlueZAdapter()
    return _ADAPTER


class SppBackend:
    def __init__(self) -> None:
        self._sock: Optional[SocketLike] = None
        self._lock = threading.Lock()
        self._connected = False
        self._channel: Optional[int] = None

    @staticmethod
    async def scan(timeout: float = 5.0) -> List[DeviceInfo]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _scan_blocking, timeout)

    async def connect(self, address: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._connect_blocking, address)

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._disconnect_blocking)

    async def write(self, data: bytes, chunk_size: int, interval_ms: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_blocking, data, chunk_size, interval_ms)

    def _connect_blocking(self, address: str) -> None:
        if self._connected:
            return
        channels = _resolve_rfcomm_channels(address)
        last_error = None
        for channel in channels:
            sock = _get_adapter().create_socket()
            set_timeout = getattr(sock, "settimeout", None)
            if callable(set_timeout):
                set_timeout(8)
            try:
                sock.connect((address, channel))
                self._sock = sock
                self._connected = True
                self._channel = channel
                return
            except OSError as exc:
                last_error = exc
                try:
                    sock.close()
                except Exception:
                    pass
        detail = f"channels tried: {channels}"
        if last_error:
            detail += f", last error: {last_error}"
        raise RuntimeError("SPP connection failed (" + detail + ")")

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

def _scan_bluetoothctl(timeout: float) -> List[DeviceInfo]:
    if not shutil.which("bluetoothctl"):
        return []
    timeout_s = max(1, int(timeout))
    try:
        subprocess.run(
            ["bluetoothctl", "--timeout", str(timeout_s), "scan", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except Exception:
        return []
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except Exception:
        return []
    devices = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = line.split(" ", 2)
        if len(parts) < 2:
            continue
        address = parts[1]
        name = parts[2] if len(parts) > 2 else ""
        devices.append(DeviceInfo(name=name, address=address))
    return _dedupe_devices(devices)


def _scan_bleak(timeout: float) -> List[DeviceInfo]:
    try:
        from bleak import BleakScanner
    except Exception:
        return []

    async def run() -> List[DeviceInfo]:
        found = await BleakScanner.discover(timeout=timeout)
        results = []
        for device in found:
            name = device.name or ""
            results.append(DeviceInfo(name=name, address=device.address))
        return results

    try:
        devices = asyncio.run(run())
    except Exception:
        return []
    return _dedupe_devices(devices)


def _scan_pybluez(timeout: float) -> List[DeviceInfo]:
    try:
        import bluetooth  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "PyBluez is required for Bluetooth scanning on Windows. "
            "Install with: pip install -r requirements.txt"
        ) from exc
    timeout_s = max(1, int(timeout))
    try:
        found = bluetooth.discover_devices(duration=timeout_s, lookup_names=True)
    except Exception as exc:
        raise RuntimeError(f"Bluetooth scan failed: {exc}") from exc
    devices = []
    for item in found:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            address, name = item[0], item[1]
        else:
            address, name = item, ""
        devices.append(DeviceInfo(name=name or "", address=address))
    return _dedupe_devices(devices)


def _dedupe_devices(devices: List[DeviceInfo]) -> List[DeviceInfo]:
    by_addr = {}
    for device in devices:
        existing = by_addr.get(device.address)
        if existing is None or (not existing.name and device.name):
            by_addr[device.address] = device
    results = list(by_addr.values())
    results.sort(key=lambda item: (item.name or "", item.address))
    return results


def _resolve_rfcomm_channels(address: str) -> List[int]:
    channel = _get_adapter().resolve_rfcomm_channel(address)
    if channel is None:
        return list(RFCOMM_CHANNELS)
    channels = [channel]
    for candidate in RFCOMM_CHANNELS:
        if candidate != channel:
            channels.append(candidate)
    return channels


def _resolve_rfcomm_channel_linux(address: str) -> Optional[int]:
    if not shutil.which("sdptool"):
        return None
    try:
        result = subprocess.run(
            ["sdptool", "browse", address],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except Exception:
        return None
    output = result.stdout or ""
    channel = None
    seen_serial = False
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Service Name:"):
            name = line.split(":", 1)[-1].strip().lower()
            seen_serial = any(key in name for key in ("serial", "spp", "printer"))
        elif line.startswith("Channel:"):
            try:
                value = int(line.split(":", 1)[-1].strip())
            except ValueError:
                value = None
            if value is None:
                continue
            if seen_serial:
                return value
            if channel is None:
                channel = value
            seen_serial = False
        elif not line:
            seen_serial = False
    return channel


def _resolve_rfcomm_channel_windows(address: str) -> Optional[int]:
    try:
        import bluetooth  # type: ignore
    except Exception:
        return None
    try:
        services = bluetooth.find_service(address=address)
    except Exception:
        return None
    preferred = None
    for service in services:
        protocol = (service.get("protocol") or "").lower()
        if protocol != "rfcomm":
            continue
        port = service.get("port")
        if port is None:
            continue
        try:
            port_value = int(port)
        except (TypeError, ValueError):
            continue
        name = (service.get("name") or "").lower()
        if any(key in name for key in ("serial", "spp", "printer")):
            return port_value
        if preferred is None:
            preferred = port_value
    return preferred
