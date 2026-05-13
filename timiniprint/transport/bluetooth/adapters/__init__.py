from __future__ import annotations

import os
from typing import Optional

from ..constants import IS_LINUX, IS_MACOS, IS_WINDOWS
from .base import _BleBluetoothAdapter, _ClassicBluetoothAdapter
from .bleak_adapter import _BleakBleAdapter
from .linux_adapter import _LinuxClassicAdapter
from .linux_l2cap_adapter import _LinuxL2capBleAdapter, linux_l2cap_supported
from .macos_adapter import _MacClassicAdapter
from .windows_adapter import _WindowsClassicAdapter

_CLASSIC_ADAPTER: Optional[_ClassicBluetoothAdapter] = None
_BLE_ADAPTER: Optional[_BleBluetoothAdapter] = None

# Env var to force one backend or the other. Useful for testing and for
# users who want to opt in/out of the L2CAP LE backend explicitly.
#   TIMINI_BLE_BACKEND=bleak       -> always use the cross-platform Bleak path
#   TIMINI_BLE_BACKEND=l2cap       -> always use the direct LE L2CAP path
#                                     (Linux only; raises a clear error elsewhere)
#   TIMINI_BLE_BACKEND=auto/unset  -> default: Bleak everywhere, except Linux
#                                     where the L2CAP backend is preferred when
#                                     available. See `internal/printer_invest…`
#                                     for the root cause this is built to work
#                                     around (BlueZ picking BR/EDR for BLE-only
#                                     printers that advertise the dual-mode flag).
_BLE_BACKEND_ENV = "TIMINI_BLE_BACKEND"


def _get_classic_adapter() -> Optional[_ClassicBluetoothAdapter]:
    global _CLASSIC_ADAPTER
    if _CLASSIC_ADAPTER is None:
        if IS_WINDOWS:
            _CLASSIC_ADAPTER = _WindowsClassicAdapter()
        elif IS_LINUX:
            _CLASSIC_ADAPTER = _LinuxClassicAdapter()
        elif IS_MACOS:
            _CLASSIC_ADAPTER = _MacClassicAdapter()
        else:
            _CLASSIC_ADAPTER = None
    return _CLASSIC_ADAPTER


def _resolve_ble_backend() -> str:
    """Resolve which BLE backend to instantiate.

    Returns one of ``"l2cap"`` or ``"bleak"``."""
    requested = (os.environ.get(_BLE_BACKEND_ENV) or "").strip().lower()
    if requested == "l2cap":
        if not linux_l2cap_supported():
            raise RuntimeError(
                f"{_BLE_BACKEND_ENV}=l2cap requested but this Python build does not "
                "support raw LE L2CAP sockets (needs Linux with socket.AF_BLUETOOTH; "
                "distro Python on Ubuntu/Debian works, pyenv needs libbluetooth-dev "
                "installed at build time)."
            )
        return "l2cap"
    if requested == "bleak":
        return "bleak"
    # Auto: prefer L2CAP on Linux when available, else Bleak.
    if requested in ("", "auto") and IS_LINUX and linux_l2cap_supported():
        return "l2cap"
    return "bleak"


def _get_ble_adapter() -> Optional[_BleBluetoothAdapter]:
    global _BLE_ADAPTER
    if _BLE_ADAPTER is None:
        if not (IS_WINDOWS or IS_LINUX or IS_MACOS):
            _BLE_ADAPTER = None
        else:
            backend = _resolve_ble_backend()
            if backend == "l2cap":
                _BLE_ADAPTER = _LinuxL2capBleAdapter()
            else:
                _BLE_ADAPTER = _BleakBleAdapter()
    return _BLE_ADAPTER


def _reset_ble_adapter_for_tests() -> None:
    """Test helper: drop the cached BLE adapter so a new env value is picked up."""
    global _BLE_ADAPTER
    _BLE_ADAPTER = None
