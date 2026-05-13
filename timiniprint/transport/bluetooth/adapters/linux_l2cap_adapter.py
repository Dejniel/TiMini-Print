"""Linux-only LE BLE adapter that uses a direct L2CAP/ATT socket.

Subclasses ``_BleakSocket`` so the high-level ``_BleakTransportSession``
plumbing (split bulk writes, runtime controllers, endpoint resolution,
chunked GATT writes, notification dispatch) is reused unchanged. Only the
``BleakClient`` construction step is overridden — we swap in
``LinuxLeL2capClient`` which bypasses ``bluetoothd``'s ``Device1.Connect()``
and the BR/EDR-first transport selection that mis-routes BLE-only printers
advertising the "Simultaneous LE and BR/EDR" flag.

See `internal/printer_investigation.md` (Appendix G, btmon excerpt) for the
root-cause analysis and `issue #23` in the upstream repo.
"""
from __future__ import annotations

from typing import Any, Optional

from ..types import DeviceInfo, DeviceTransport, SocketLike
from .base import _BleBluetoothAdapter
from .bleak_adapter import _BleakBleAdapter, _BleakSocket
from .linux_l2cap_client import LinuxLeL2capClient, af_bluetooth_available
from .... import reporting
from ....protocol.family import ProtocolFamily


class _LinuxL2capLeSocket(_BleakSocket):
    """``_BleakSocket`` subclass that connects via raw LE L2CAP."""

    async def _connect_async(self, address: str) -> None:
        # Mirror _BleakSocket._connect_async but use LinuxLeL2capClient
        # instead of BleakClient. The rest of the session setup
        # (notification subscribe, transport binding) is identical.
        self._client = LinuxLeL2capClient(address)
        try:
            await self._client.connect()
            self._connected = True
        except Exception as exc:
            detail = str(exc).strip() or repr(exc) or exc.__class__.__name__
            raise RuntimeError(
                f"Failed to connect to BLE device {address} via L2CAP: {detail}"
            ) from exc

        if self._client.mtu_size:
            negotiated_mtu = self._client.mtu_size - 3
            self._mtu_size = min(negotiated_mtu, 512)

        selection = await self._find_write_characteristic()
        if not selection:
            await self._client.disconnect()
            self._connected = False
            raise RuntimeError(
                f"Could not find a writable GATT characteristic on device {address}. "
                "The device may not support BLE printing, or uses unknown UUIDs."
            )

        self._transport.apply_write_selection(selection)
        self._transport.configure_endpoints(
            getattr(self._client, "services", None) or []
        )
        await self._transport.start_notify_if_available(
            self._client, self._handle_notification
        )
        await self._transport.initialize_connection(
            self._client,
            mtu_size=self._mtu_size,
            timeout=self._timeout,
        )


class _LinuxL2capBleAdapter(_BleBluetoothAdapter):
    """LE adapter that opens raw L2CAP/ATT sockets via :class:`LinuxLeL2capClient`.

    Discovery still rides on Bleak's BlueZ-backed scanner, which works fine —
    only the connect step is the broken one.
    """

    transport = DeviceTransport.BLE

    def __init__(self, scanner: Optional[_BleakBleAdapter] = None) -> None:
        # Reuse the Bleak adapter purely for its scan-side helpers and cache.
        self._scanner = scanner or _BleakBleAdapter()

    def scan_blocking(self, timeout: float):
        return self._scanner.scan_blocking(timeout)

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        return _LinuxL2capLeSocket(
            pairing_hint=pairing_hint,
            protocol_family=protocol_family,
            reporter=reporter,
        )


def linux_l2cap_supported() -> bool:
    """Return True iff this host can use the L2CAP LE backend.

    Requires Linux and a Python build with ``socket.AF_BLUETOOTH`` /
    ``BTPROTO_L2CAP`` (distro Python on Ubuntu/Debian, or pyenv built with
    ``libbluetooth-dev`` installed at compile time)."""
    return af_bluetooth_available()
