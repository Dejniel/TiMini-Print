from __future__ import annotations

from typing import Optional

from ..constants import IS_LINUX, IS_MACOS, IS_WINDOWS
from .base import _BleBluetoothAdapter, _ClassicBluetoothAdapter
from .bleak_adapter import _BleakBleAdapter
from .linux_adapter import _LinuxClassicAdapter
from .windows_adapter import _WindowsClassicAdapter

_CLASSIC_ADAPTER: Optional[_ClassicBluetoothAdapter] = None
_BLE_ADAPTER: Optional[_BleBluetoothAdapter] = None


def _get_classic_adapter() -> Optional[_ClassicBluetoothAdapter]:
    global _CLASSIC_ADAPTER
    if _CLASSIC_ADAPTER is None:
        if IS_WINDOWS:
            _CLASSIC_ADAPTER = _WindowsClassicAdapter()
        elif IS_LINUX:
            _CLASSIC_ADAPTER = _LinuxClassicAdapter()
        else:
            _CLASSIC_ADAPTER = None
    return _CLASSIC_ADAPTER


def _get_ble_adapter() -> Optional[_BleBluetoothAdapter]:
    global _BLE_ADAPTER
    if _BLE_ADAPTER is None:
        if IS_WINDOWS or IS_LINUX or IS_MACOS:
            _BLE_ADAPTER = _BleakBleAdapter()
        else:
            _BLE_ADAPTER = None
    return _BLE_ADAPTER
