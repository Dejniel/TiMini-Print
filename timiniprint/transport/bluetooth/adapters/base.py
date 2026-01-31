from __future__ import annotations

from typing import List, Optional

from ..types import DeviceInfo, DeviceTransport, SocketLike


class _BaseBluetoothAdapter:
    transport: DeviceTransport
    single_channel = False

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        raise NotImplementedError

    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return None

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None


class _ClassicBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.CLASSIC


class _BleBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.BLE
    single_channel = True

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return 1
