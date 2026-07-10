from __future__ import annotations

import socket
from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from ....devices.bluetooth_profiles import BleTransportProfile
from .linux_cmd import LinuxCommandTools
from ..types import DeviceInfo, SocketLike
from .... import reporting


class _LinuxClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._commands = LinuxCommandTools()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, _ = self._commands.scan_devices(timeout)
        return DeviceInfo.dedupe(devices)

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        ble_profile: BleTransportProfile | None = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        ble_mtu_request: Optional[int] = None,
    ) -> SocketLike:
        _ = ble_mtu_request
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError(
                "RFCOMM sockets are not supported on this system. Use --serial or run on Linux."
            )
        _ = ble_profile
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return self._commands.resolve_rfcomm_channels(address)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._commands.ensure_paired(address)
