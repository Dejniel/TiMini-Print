from __future__ import annotations

from typing import List, Optional

from ..types import DeviceInfo, DeviceTransport, SocketLike
from .... import reporting


class _BaseBluetoothAdapter:
    transport: DeviceTransport

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        raise NotImplementedError

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return []

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None


class _ClassicBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.CLASSIC


class _BleBluetoothAdapter(_BaseBluetoothAdapter):
    transport = DeviceTransport.BLE

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return [1]
