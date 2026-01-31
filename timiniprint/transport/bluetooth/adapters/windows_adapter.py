from __future__ import annotations

from typing import List, Optional

from .base import _ClassicBluetoothAdapter
from ..constants import RFCOMM_CHANNELS
from ..types import DeviceInfo, SocketLike
from .windows_win32 import _Win32ClassicBackend
from .windows_winrt import _WinRtClassicBackend


class _WindowsClassicAdapter(_ClassicBluetoothAdapter):
    single_channel = True

    def __init__(self) -> None:
        self._win32 = _Win32ClassicBackend()
        self._winrt = _WinRtClassicBackend()

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices = self._win32.scan_inquiry(timeout)
        devices = DeviceInfo.dedupe(devices)
        try:
            winrt_devices = self._winrt.scan_blocking(timeout)
            if winrt_devices:
                devices = DeviceInfo.dedupe(devices + winrt_devices)
        except Exception:
            pass
        return devices

    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        return self._winrt.create_socket()

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return RFCOMM_CHANNELS[0]

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        winrt_error = None
        win32_error = None
        win32_paired = False
        try:
            self._winrt.ensure_paired(address)
        except Exception as exc:
            winrt_error = exc
        if not self._winrt.has_service(address):
            try:
                self._winrt.refresh_mapping(5.0)
            except Exception:
                pass
        needs_win32 = winrt_error is not None or not self._winrt.has_service(address)
        if needs_win32:
            try:
                win32_paired = self._win32.pair_device(address)
                if not win32_paired:
                    win32_error = RuntimeError("pairing failed (Win32 returned False)")
            except Exception as exc:
                win32_error = exc
            if not self._winrt.has_service(address):
                try:
                    self._winrt.refresh_mapping(5.0)
                except Exception:
                    pass
        if winrt_error and not win32_paired:
            if win32_error:
                raise RuntimeError(f"pairing failed (WinRT: {winrt_error}; Win32: {win32_error})")
            raise RuntimeError(f"pairing failed (WinRT: {winrt_error})")
        if win32_error and not self._winrt.has_service(address):
            raise RuntimeError(f"pairing failed (Win32: {win32_error})")
