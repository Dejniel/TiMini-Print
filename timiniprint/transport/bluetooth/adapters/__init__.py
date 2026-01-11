from __future__ import annotations

from typing import Optional

from ..constants import IS_LINUX, IS_WINDOWS
from .base import _BluetoothAdapter
from .linux_adapter import _LinuxBluetoothAdapter
from .windows_adapter import _WindowsBluetoothAdapter

_ADAPTER: Optional[_BluetoothAdapter] = None


def _get_adapter() -> _BluetoothAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        if IS_WINDOWS:
            _ADAPTER = _WindowsBluetoothAdapter()
        elif IS_LINUX:
            _ADAPTER = _LinuxBluetoothAdapter()
        else:
            raise RuntimeError(
                "Bluetooth SPP is supported only on Linux or Windows. "
                "Try the CLI with --serial and provide an RFCOMM socket path if available."
            )
    return _ADAPTER
