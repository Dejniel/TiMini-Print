from __future__ import annotations

from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from ....devices.bluetooth_profiles import BleTransportProfile
from .macos_iobluetooth import _MacClassicBackend
from ..types import DeviceInfo, SocketLike
from .... import reporting


class _MacClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._backend = _MacClassicBackend()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        return DeviceInfo.dedupe(self._backend.scan_inquiry(timeout))

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        ble_profile: BleTransportProfile | None = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        ble_mtu_request: Optional[int] = None,
    ) -> SocketLike:
        _ = ble_profile
        _ = ble_mtu_request
        return self._backend.create_socket()

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        return self._backend.resolve_rfcomm_channels(address)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._backend.pair_device(address)
