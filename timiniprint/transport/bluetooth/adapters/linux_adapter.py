from __future__ import annotations

import socket
from typing import List, Optional

from .base import _BluetoothAdapter
from .linux_cmd import LinuxCommandTools
from ..types import DeviceInfo, SocketLike


class _LinuxBluetoothAdapter(_BluetoothAdapter):
    def __init__(self) -> None:
        self._commands = LinuxCommandTools()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, paired_addresses = self._commands.scan_devices(timeout)
        bleak_devices = self._scan_bleak(timeout, paired_addresses)
        return DeviceInfo.dedupe(devices + bleak_devices)

    def create_socket(self) -> SocketLike:
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError(
                "RFCOMM sockets are not supported on this system. Use --serial or run on Linux."
            )
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return self._commands.resolve_rfcomm_channel(address)

    def ensure_paired(self, address: str) -> None:
        self._commands.ensure_paired(address)
