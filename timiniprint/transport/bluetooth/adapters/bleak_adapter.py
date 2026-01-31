"""Bluetooth Low Energy adapter using bleak for BLE communication."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from .base import _BleBluetoothAdapter
from ..constants import IS_MACOS
from ..types import DeviceInfo, DeviceTransport, SocketLike


def _missing_bleak_error() -> RuntimeError:
    return RuntimeError(
        "bleak is required for BLE Bluetooth support. Install it with: pip install bleak"
    )


class _BleakSocket:
    """Socket-like wrapper around a bleak BLE client for GATT write operations."""

    def __init__(self, pairing_hint: Optional[bool] = None) -> None:
        self._client: Any = None
        self._write_char: Any = None
        self._address: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mtu_size = 180  # Start with a reasonable size, will negotiate larger if possible
        self._timeout = 30.0
        # BLE thermal printers need longer delays than classic Bluetooth
        self._write_delay_ms = 50  # ms between BLE GATT writes
        self._pairing_hint = pairing_hint is True and not IS_MACOS

    def settimeout(self, timeout: float) -> None:
        """Set socket timeout (stored for use in async operations)."""
        self._timeout = timeout

    def connect(self, address_channel: Tuple[str, int]) -> None:
        """Connect to a BLE device.
        
        Args:
            address_channel: Tuple of (address, channel). Channel is ignored for BLE.
                           Address can be MAC address or macOS UUID.
        """
        address, _ = address_channel
        self._address = address

        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._connect_async(address))
        except Exception:
            self._cleanup_loop()
            raise

    async def _connect_async(self, address: str) -> None:
        """Async connection to BLE device."""
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        # On macOS, we might have a UUID instead of MAC address
        # Try to find the device first
        device = None
        
        # Check if address looks like a UUID (macOS style) or MAC address
        is_uuid = len(address) == 36 and address.count("-") == 4
        
        if is_uuid:
            # Direct connection with UUID
            self._client = BleakClient(address)
        else:
            # Try to find device by MAC address through scanning
            devices = await BleakScanner.discover(timeout=5.0)
            for dev in devices:
                # On macOS, dev.address is a UUID, but we can check metadata
                if hasattr(dev, "details") and dev.details:
                    # Try to match by name or address in metadata
                    pass
                # Also check if the name matches (some devices include MAC in name)
                if dev.address.upper() == address.upper():
                    device = dev
                    break
                if dev.name and address.upper() in dev.name.upper():
                    device = dev
                    break
            
            if device:
                self._client = BleakClient(device)
            else:
                # Try connecting directly with the address anyway
                self._client = BleakClient(address)

        try:
            await self._client.connect()
            self._connected = True
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to BLE device {address}: {exc}") from exc

        # Update MTU size if available (ATT MTU minus 3 bytes header overhead)
        if hasattr(self._client, "mtu_size") and self._client.mtu_size:
            negotiated_mtu = self._client.mtu_size - 3
            # Use the negotiated MTU but cap at a reasonable size for thermal printers
            self._mtu_size = min(negotiated_mtu, 512)

        # Pair on platforms that support it if hinted.
        if self._pairing_hint:
            await self._pair_if_supported()

        # Discover services and find write characteristic
        self._write_char = await self._find_write_characteristic()
        if not self._write_char:
            await self._client.disconnect()
            self._connected = False
            raise RuntimeError(
                f"Could not find a writable GATT characteristic on device {address}. "
                "The device may not support BLE printing, or uses unknown UUIDs."
            )

    async def _find_write_characteristic(self) -> Optional[Any]:
        """Find a suitable write characteristic on the connected device."""
        if not self._client or not self._connected:
            return None

        services = self._client.services

        # Find first writable characteristic; prefer write-with-response for reliability.
        for service in services:
            for char in service.characteristics:
                props = char.properties
                if "write" in props:
                    return char
        for service in services:
            for char in service.characteristics:
                props = char.properties
                if "write-without-response" in props:
                    return char

        return None

    def send(self, data: bytes) -> int:
        """Send data to the BLE device."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to BLE device")
        if not self._loop:
            raise RuntimeError("Event loop not initialized")

        try:
            self._loop.run_until_complete(self._send_async(data))
            return len(data)
        except Exception as exc:
            raise RuntimeError(f"BLE write failed: {exc}") from exc

    def sendall(self, data: bytes) -> None:
        """Send all data to the BLE device."""
        self.send(data)

    async def _send_async(self, data: bytes) -> None:
        """Async send data via GATT write."""
        if not self._write_char:
            raise RuntimeError("No write characteristic available")

        # For thermal printers, prefer write-with-response for reliability
        # Only use write-without-response if it's the only option
        props = self._write_char.properties
        if "write" in props:
            response = True  # Use write-with-response for reliability
        elif "write-without-response" in props:
            response = False
        else:
            raise RuntimeError("Characteristic does not support writing")
        
        # BLE has MTU limitations - use negotiated MTU or fallback
        # Most thermal printers work well with 20-byte chunks for maximum compatibility
        chunk_size = min(self._mtu_size, 20)  # Conservative chunk size for reliability
        
        delay_seconds = self._write_delay_ms / 1000.0
        
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            await self._client.write_gatt_char(self._write_char, chunk, response=response)
            # Thermal printers need time to process each chunk
            # This delay is critical for reliable printing
            await asyncio.sleep(delay_seconds)

    async def _pair_if_supported(self) -> None:
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
        """Close the BLE connection."""
        if self._loop and self._client and self._connected:
            try:
                self._loop.run_until_complete(self._client.disconnect())
            except Exception:
                pass
        self._connected = False
        self._cleanup_loop()

    def _cleanup_loop(self) -> None:
        """Clean up the event loop."""
        if self._loop:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None


class _BleakBleAdapter(_BleBluetoothAdapter):
    """Bluetooth Low Energy adapter using bleak for GATT writes."""

    def __init__(self) -> None:
        self._device_cache: Dict[str, DeviceInfo] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        """Scan for BLE devices."""
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise _missing_bleak_error() from exc

        async def scan() -> List[DeviceInfo]:
            devices = await BleakScanner.discover(timeout=timeout)
            results = []
            for device in devices:
                name = device.name or ""
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

        # Cache devices for later connection
        for device in devices:
            self._device_cache[device.address] = device

        return devices

    def create_socket(self, pairing_hint: Optional[bool] = None) -> SocketLike:
        """Create a BLE socket-like object for communication."""
        return _BleakSocket(pairing_hint=pairing_hint)

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        # BLE pairing is handled during connect if requested and supported.
        return None
