from __future__ import annotations

from typing import Optional

from ..constants import IS_LINUX, IS_MACOS, IS_WINDOWS
from .base import _BluetoothAdapter
from .linux_adapter import _LinuxBluetoothAdapter
from .macos_adapter import _MacOSBluetoothAdapter
from .windows_adapter import _WindowsBluetoothAdapter

_ADAPTER: Optional[_BluetoothAdapter] = None


def _get_adapter() -> _BluetoothAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        if IS_WINDOWS:
            _ADAPTER = _WindowsBluetoothAdapter()
        elif IS_LINUX:
            _ADAPTER = _LinuxBluetoothAdapter()
        elif IS_MACOS:
            _ADAPTER = _MacOSBluetoothAdapter()
        else:
            raise RuntimeError(
                "Bluetooth is supported only on Linux, Windows, or macOS. "
                "Try the CLI with --serial and provide a serial port path if available."
            )
    return _ADAPTER
