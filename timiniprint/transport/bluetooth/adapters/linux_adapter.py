from __future__ import annotations

import socket
from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from .linux_cmd import LinuxCommandTools
from ....protocol.family import ProtocolFamily
from ..types import DeviceInfo, SocketLike
from .... import reporting

_LINUX_AF_BLUETOOTH = 31
_LINUX_BTPROTO_RFCOMM = 3


class _LinuxClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._commands = LinuxCommandTools()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, _ = self._commands.scan_devices(timeout)
        return DeviceInfo.dedupe(devices)

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        family = getattr(socket, "AF_BLUETOOTH", _LINUX_AF_BLUETOOTH)
        proto = getattr(socket, "BTPROTO_RFCOMM", _LINUX_BTPROTO_RFCOMM)
        _ = protocol_family
        try:
            return socket.socket(family, socket.SOCK_STREAM, proto)
        except OSError as exc:
            raise RuntimeError(
                "RFCOMM sockets are not supported by this Python runtime. "
                "Use --serial or a Linux Python build with Bluetooth socket support."
            ) from exc

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return self._commands.resolve_rfcomm_channels(address)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._commands.ensure_paired(address)
