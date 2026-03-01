from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from .base import _ClassicBluetoothAdapter
from .macos_iobluetooth import _MacClassicBackend
from ..types import DeviceInfo, SocketLike

if TYPE_CHECKING:
    from .... import reporting


class _MacClassicAdapter(_ClassicBluetoothAdapter):
    def __init__(self) -> None:
        self._backend = _MacClassicBackend()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        return DeviceInfo.dedupe(self._backend.scan_inquiry(timeout))

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        reporter: Optional["reporting.Reporter"] = None,
    ) -> SocketLike:
        return self._backend.create_socket()

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return self._backend.resolve_rfcomm_channel(address)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        self._backend.pair_device(address)
