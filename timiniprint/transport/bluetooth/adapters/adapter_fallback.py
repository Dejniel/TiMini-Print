"""Generic BLE adapter fallback wrapper."""
from __future__ import annotations

from typing import List, Optional

from .base import _BleBluetoothAdapter
from ..types import DeviceInfo, SocketLike
from .... import reporting
from ....protocol.family import ProtocolFamily


class _FallbackAdapter(_BleBluetoothAdapter):
    """Try one BLE backend, then fall back to another on connect failure."""

    def __init__(self, primary: _BleBluetoothAdapter, fallback: _BleBluetoothAdapter) -> None:
        self._primary = primary
        self._fallback = fallback

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        return self._fallback.scan_blocking(timeout)

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        ble_mtu_request: Optional[int] = None,
    ) -> SocketLike:
        return _FallbackSocket(
            primary=self._primary.create_socket(
                pairing_hint=pairing_hint,
                protocol_family=protocol_family,
                reporter=reporter,
                ble_mtu_request=ble_mtu_request,
            ),
            fallback=self._fallback.create_socket(
                pairing_hint=pairing_hint,
                protocol_family=protocol_family,
                reporter=reporter,
                ble_mtu_request=ble_mtu_request,
            ),
            primary_name="linux-att",
            fallback_name="bleak",
            reporter=reporter,
        )

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None


class _FallbackSocket:
    """Socket facade that activates the first backend that connects."""

    def __init__(
        self,
        *,
        primary: SocketLike,
        fallback: SocketLike,
        primary_name: str,
        fallback_name: str,
        reporter: reporting.Reporter,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._reporter = reporter
        self._timeout = 30.0
        self._active: SocketLike | None = None

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout
        _call_if_present(self._primary, "settimeout", timeout)
        _call_if_present(self._fallback, "settimeout", timeout)

    def connect(self, address_channel) -> None:
        address, _channel = address_channel
        primary_error = self._try_connect(self._primary, self._primary_name, address_channel, address)
        if primary_error is None:
            return
        self._reporter.debug(
            short="BLE",
            detail=(
                f"BLE backend {self._primary_name} failed, falling back to "
                f"{self._fallback_name}: address={address} error={primary_error}"
            ),
        )
        fallback_error = self._try_connect(self._fallback, self._fallback_name, address_channel, address)
        if fallback_error is None:
            return
        raise RuntimeError(
            "BLE connection failed "
            f"({self._primary_name} error: {primary_error}; "
            f"{self._fallback_name} error: {fallback_error})"
        )

    def close(self) -> None:
        active = self._active
        self._active = None
        _safe_close(active)
        if active is not self._primary:
            _safe_close(self._primary)
        if active is not self._fallback:
            _safe_close(self._fallback)

    def _try_connect(self, sock: SocketLike, backend_name: str, address_channel, address: str) -> Exception | None:
        try:
            sock.connect(address_channel)
        except Exception as exc:
            _safe_close(sock)
            return exc
        self._active = sock
        self._reporter.debug(
            short="BLE",
            detail=f"BLE backend selected: {backend_name} address={address}",
        )
        return None

    def __getattr__(self, name: str):
        active = self._active or self._fallback
        return getattr(active, name)


def _call_if_present(target: SocketLike, name: str, *args) -> None:
    method = getattr(target, name, None)
    if callable(method):
        method(*args)


def _safe_close(sock: SocketLike | None) -> None:
    if sock is None:
        return
    close = getattr(sock, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
