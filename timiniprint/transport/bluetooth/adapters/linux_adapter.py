from __future__ import annotations

import socket
from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from .linux_cmd import LinuxCommandTools
from ..types import DeviceInfo, SocketLike


class _LinuxClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._commands = LinuxCommandTools()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, _ = self._commands.scan_devices(timeout)
        return DeviceInfo.dedupe(devices)

    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError(
                "RFCOMM sockets are not supported on this system. Use --serial or run on Linux."
            )
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return self._commands.resolve_rfcomm_channel(address)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._commands.ensure_paired(address)
