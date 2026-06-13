"""Bluetooth Low Energy adapter using bleak for BLE communication.

The adapter keeps connection lifecycle in `_BleakSocket` and delegates endpoint
binding plus family-aware write routing to `_BleakTransportSession`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Dict, List, Optional, Tuple

from .base import _BleBluetoothAdapter
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver, _WriteSelection
from .bleak_adapter_transport import _BleakTransportSession
from ..constants import IS_MACOS
from ..types import DeviceInfo, DeviceTransport, SocketLike
from .... import reporting
from ....protocol.families import get_protocol_behavior
from ....protocol.family import ProtocolFamily

_BLE_DEFAULT_MTU_PAYLOAD = 20


def _missing_bleak_error() -> RuntimeError:
    return RuntimeError(
        "bleak is required for BLE Bluetooth support. Install it with: pip install bleak"
    )


class _BleakSocket:
    """Socket-like wrapper around a bleak BLE client.

    It owns connection setup/teardown and uses `_BleakTransportSession` for the
    protocol-specific parts of write routing and notify handling.
    """

    def __init__(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        device_cache: Optional[Dict[str, Any]] = None,
        ble_mtu_request: Optional[int] = None,
    ) -> None:
        self._client: Any = None
        self._address: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mtu_size = _BLE_DEFAULT_MTU_PAYLOAD
        self._timeout = 30.0
        self._pairing_hint = pairing_hint is True and not IS_MACOS
        self._protocol_family = protocol_family
        self._ble_mtu_request = ble_mtu_request
        self._reporter = reporter
        self._device_cache = device_cache if device_cache is not None else {}
        self._write_resolver = _BleWriteEndpointResolver(reporter=self._reporter)
        self._transport = _BleakTransportSession(
            protocol_family=self._protocol_family_or_default(),
            transport_profile=get_protocol_behavior(self._protocol_family_or_default()).transport,
            write_resolver=self._write_resolver,
            reporter=self._reporter,
        )

    def settimeout(self, timeout: float) -> None:
        """Store the timeout used by async BLE operations."""
        self._timeout = timeout

    @property
    def _flow_can_write(self) -> bool:
        return self._transport.flow_can_write

    @_flow_can_write.setter
    def _flow_can_write(self, value: bool) -> None:
        self._transport.flow_can_write = value

    @property
    def _notify_started(self) -> bool:
        return self._transport.notify_started

    @_notify_started.setter
    def _notify_started(self, value: bool) -> None:
        self._transport.notify_started = value

    def connect(self, address_channel: Tuple[str, int]) -> None:
        """Connect to the BLE device and prepare family-specific endpoints."""
        address, _ = address_channel
        self._address = address
        previous_loop = None

        try:
            try:
                previous_loop = asyncio.get_event_loop()
            except RuntimeError:
                previous_loop = None
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._connect_async(address))
        except Exception:
            self._disconnect_after_failed_connect()
            self._cleanup_loop()
            raise
        finally:
            try:
                asyncio.set_event_loop(previous_loop)
            except Exception:
                pass

    async def _connect_async(self, address: str) -> None:
        """Create the client, connect it and bind writable characteristics."""
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        self._client = BleakClient(await self._resolve_client_target(address))

        try:
            await self._client.connect()
            self._connected = True
        except Exception as exc:
            detail = str(exc).strip() or repr(exc) or exc.__class__.__name__
            raise RuntimeError(f"Failed to connect to BLE device {address}: {detail}") from exc

        if (
            self._ble_mtu_request is not None
            and hasattr(self._client, "mtu_size")
            and self._client.mtu_size
        ):
            negotiated_mtu = min(self._client.mtu_size, self._ble_mtu_request)
            self._mtu_size = max(1, min(negotiated_mtu - 3, 512))

        if self._pairing_hint:
            await self._pair_if_supported()

        selection = await self._find_write_characteristic()
        if not selection:
            await self._client.disconnect()
            self._connected = False
            raise RuntimeError(
                f"Could not find a writable GATT characteristic on device {address}. "
                "The device may not support BLE printing, or uses unknown UUIDs."
            )

        self._transport.apply_write_selection(selection)
        self._transport.configure_endpoints(getattr(self._client, "services", None) or [])
        await self._transport.start_notify_if_available(self._client, self._handle_notification)
        await self._transport.initialize_connection(
            self._client,
            mtu_size=self._mtu_size,
            timeout=self._timeout,
        )

    async def _resolve_client_target(self, address: str) -> Any:
        """Return the address or discovered device object passed to BleakClient."""
        # On macOS, reuse of a cached BLEDevice from a previous scan loop can
        # fail with "Future attached to a different loop". Pass the CoreBluetooth
        # address string directly instead.
        if IS_MACOS:
            return address
        cached = self._device_cache.get(address.upper())
        if cached is not None:
            return cached

        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        devices = await BleakScanner.discover(timeout=5.0)
        for dev in devices:
            if dev.address.upper() == address.upper():
                return dev
            if dev.name and address.upper() in dev.name.upper():
                return dev
        return address

    async def _find_write_characteristic(self) -> Optional[_WriteSelection]:
        """Resolve the primary writable characteristic for this connection."""
        if not self._client or not self._connected:
            return None
        transport = get_protocol_behavior(self._protocol_family_or_default()).transport
        return self._write_resolver.resolve(
            self._client.services,
            preferred_service_uuid=transport.preferred_service_uuid,
            preferred_write_char_uuid=transport.preferred_write_char_uuid,
        )

    def send(self, data: bytes) -> int:
        return self.send_payload(data)

    def send_payload(self, data: bytes, runtime_controller=None) -> int:
        """Send one payload using the active BLE transport session."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to BLE device")
        if not self._loop:
            raise RuntimeError("Event loop not initialized")

        try:
            self._loop.run_until_complete(self._send_async(data, runtime_controller=runtime_controller))
            return len(data)
        except Exception as exc:
            bindings = self._transport.bindings
            detail = (
                f"service={bindings.write_service_uuid} "
                f"char={bindings.write_char_uuid}"
            )
            raise RuntimeError(f"BLE write failed ({detail}): {exc}") from exc

    def sendall(self, data: bytes) -> None:
        """Compatibility alias matching socket-style APIs."""
        self.send_payload(data)

    def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0):
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to BLE device")
        if not self._loop:
            raise RuntimeError("Event loop not initialized")
        self._loop.run_until_complete(
            self._transport.attach_runtime_controller(
                runtime_controller,
                mtu_size=self._mtu_size,
                timeout=timeout,
            )
        )

    def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        if not self._connected or not self._client:
            return False
        if not self._loop:
            raise RuntimeError("Event loop not initialized")
        return self._loop.run_until_complete(
            self._transport.send_control_packet(packet, timeout=timeout)
        )

    def can_send_control_packet(self) -> bool:
        if not self._connected or not self._client:
            return False
        return self._transport.can_send_control_packet()

    def can_query_control_packet(self) -> bool:
        if not self._connected or not self._client:
            return False
        return self._transport.can_query_control_packet()

    def can_wait_for_notification(self) -> bool:
        if not self._connected or not self._client:
            return False
        return self._transport.can_wait_for_notification()

    def can_send_control_packet_wait_notification(self) -> bool:
        if not self._connected or not self._client:
            return False
        return self._transport.can_send_control_packet_wait_notification()

    def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        if not self._connected or not self._client:
            return None
        if not self._loop:
            raise RuntimeError("Event loop not initialized")
        return self._loop.run_until_complete(
            self._transport.query_control_packet(
                packet,
                timeout=timeout,
                reply_complete=reply_complete,
            )
        )

    def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        if not self._connected or not self._client:
            if required:
                raise RuntimeError("Not connected to BLE device")
            return None
        if not self._loop:
            raise RuntimeError("Event loop not initialized")
        return self._loop.run_until_complete(
            self._transport.wait_for_notification(
                label,
                match,
                timeout=timeout,
                required=required,
            )
        )

    def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match: Callable[[bytes], bool],
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        if not self._connected or not self._client:
            if required:
                raise RuntimeError("Not connected to BLE device")
            return None
        if not self._loop:
            raise RuntimeError("Event loop not initialized")
        return self._loop.run_until_complete(
            self._transport.send_control_packet_wait_notification(
                packet,
                label=label,
                match=match,
                timeout=timeout,
                required=required,
            )
        )

    async def _send_async(self, data: bytes, runtime_controller=None) -> None:
        """Delegate payload routing and chunking to the transport session."""
        await self._transport.send(
            self._client,
            data,
            mtu_size=self._mtu_size,
            timeout=self._timeout,
            runtime_controller=runtime_controller,
        )

    async def _pair_if_supported(self) -> None:
        """Run platform pairing when the bleak client exposes it."""
        pair = getattr(self._client, "pair", None)
        if not callable(pair):
            return
        try:
            result = await pair()
        except Exception as exc:
            raise RuntimeError(f"BLE pairing failed: {exc}") from exc
        if result is False:
            raise RuntimeError("BLE pairing failed")

    def close(self) -> None:
        """Close the BLE connection and release the private event loop."""
        self._disconnect_after_failed_connect()
        self._cleanup_loop()

    def _disconnect_after_failed_connect(self) -> None:
        """Best-effort disconnect path shared by connect failures and close()."""
        if self._loop and self._client:
            try:
                self._loop.run_until_complete(self._safe_disconnect_async())
            except Exception:
                pass
        self._connected = False
        self._client = None
        self._transport = _BleakTransportSession(
            protocol_family=self._protocol_family_or_default(),
            transport_profile=get_protocol_behavior(self._protocol_family_or_default()).transport,
            write_resolver=self._write_resolver,
            reporter=self._reporter,
        )

    async def _safe_disconnect_async(self) -> None:
        """Stop notifications before disconnecting the bleak client."""
        if not self._client:
            return
        self._transport.report_disconnect_diagnostics()
        await self._transport.stop_notify_if_started(self._client)
        disconnect = getattr(self._client, "disconnect", None)
        if not callable(disconnect):
            return
        try:
            await disconnect()
        except Exception:
            pass

    def _cleanup_loop(self) -> None:
        """Dispose the temporary event loop used by the socket wrapper."""
        if self._loop:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    def _protocol_family_or_default(self) -> ProtocolFamily:
        return ProtocolFamily.from_value(self._protocol_family)

    def _handle_notification(self, _sender: Any, data: Any) -> None:
        self._transport.handle_notification(bytes(data))


class _BleakBleAdapter(_BleBluetoothAdapter):
    """Bluetooth Low Energy adapter using bleak for GATT writes."""

    def __init__(self) -> None:
        self._device_cache: Dict[str, Any] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        async def scan() -> List[DeviceInfo]:
            devices = await BleakScanner.discover(timeout=timeout)
            results = []
            for device in devices:
                name = device.name or ""
                self._device_cache[device.address.upper()] = device
                results.append(
                    DeviceInfo(
                        name=name,
                        address=device.address,
                        paired=None,
                        transport=DeviceTransport.BLE,
                    )
                )
            return results

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                devices = loop.run_until_complete(scan())
            finally:
                loop.close()
        except Exception as exc:
            raise RuntimeError(f"BLE scan failed: {exc}") from exc

        return devices

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
        ble_mtu_request: Optional[int] = None,
    ) -> SocketLike:
        return _BleakSocket(
            pairing_hint=pairing_hint,
            protocol_family=protocol_family,
            reporter=reporter,
            device_cache=self._device_cache,
            ble_mtu_request=ble_mtu_request,
        )

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None
