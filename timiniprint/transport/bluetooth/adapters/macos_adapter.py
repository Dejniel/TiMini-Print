"""macOS Bluetooth adapter using bleak for BLE communication.

macOS does not support Bluetooth Classic RFCOMM sockets natively like Linux.
This adapter uses Bluetooth Low Energy (BLE) via the bleak library, which works
well on macOS via CoreBluetooth.

Many thermal printers support BLE with GATT characteristics for data transmission.
Common service/characteristic UUIDs are tried automatically.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .base import _BluetoothAdapter
from ..types import DeviceInfo, SocketLike


# Common BLE service and characteristic UUIDs used by thermal printers
# Nordic UART Service (NUS) - very common
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to printer
NUS_RX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notifications from printer

# Common thermal printer service UUIDs
COMMON_PRINTER_SERVICES = [
    NUS_SERVICE_UUID,
    "0000ff00-0000-1000-8000-00805f9b34fb",  # Common for Cat Printer and similar
    "0000ae30-0000-1000-8000-00805f9b34fb",  # Some Asian thermal printers
    "49535343-fe7d-4ae5-8fa9-9fafd205e455",  # Microchip BLE UART
    "0000fff0-0000-1000-8000-00805f9b34fb",  # Generic printer service
]

# Common write characteristic UUIDs
COMMON_WRITE_CHARS = [
    NUS_TX_CHAR_UUID,
    "0000ff02-0000-1000-8000-00805f9b34fb",  # Cat Printer write char
    "0000ae01-0000-1000-8000-00805f9b34fb",  # Some Asian thermal printers
    "49535343-8841-43f4-a8d4-ecbe34729bb3",  # Microchip BLE UART TX
    "0000fff2-0000-1000-8000-00805f9b34fb",  # Generic write char
]


class _BleakSocket:
    """Socket-like wrapper around a bleak BLE client for GATT write operations."""

    def __init__(self, adapter: "_MacOSBluetoothAdapter") -> None:
        self._adapter = adapter
        self._client: Any = None
        self._write_char: Any = None
        self._address: Optional[str] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mtu_size = 180  # Start with a reasonable size, will negotiate larger if possible
        self._timeout = 30.0
        # BLE thermal printers need longer delays than classic Bluetooth
        self._write_delay_ms = 50  # ms between BLE GATT writes

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
            from bleak.exc import BleakError
        except ImportError as exc:
            raise RuntimeError(
                "bleak is required for macOS Bluetooth support. "
                "Install it with: pip install bleak"
            ) from exc

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
        
        # First, try known printer service/characteristic UUIDs
        for service_uuid in COMMON_PRINTER_SERVICES:
            service = services.get_service(service_uuid)
            if service:
                for char_uuid in COMMON_WRITE_CHARS:
                    char = service.get_characteristic(char_uuid)
                    if char and ("write" in char.properties or "write-without-response" in char.properties):
                        return char

        # If no known service found, search all services for writable characteristics
        for service in services:
            for char in service.characteristics:
                props = char.properties
                if "write" in props or "write-without-response" in props:
                    # Prefer write-without-response for better throughput
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


class _MacOSBluetoothAdapter(_BluetoothAdapter):
    """macOS Bluetooth adapter using bleak for BLE communication."""

    # BLE doesn't use RFCOMM channels
    single_channel = True

    def __init__(self) -> None:
        self._device_cache: Dict[str, DeviceInfo] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        """Scan for BLE devices.
        
        On macOS, CoreBluetooth uses UUIDs instead of MAC addresses.
        The returned addresses will be these UUIDs.
        """
        try:
            from bleak import BleakScanner
        except ImportError:
            return []

        async def scan() -> List[DeviceInfo]:
            devices = await BleakScanner.discover(timeout=timeout)
            results = []
            for device in devices:
                name = device.name or ""
                # On macOS, device.address is a CoreBluetooth UUID
                results.append(DeviceInfo(
                    name=name,
                    address=device.address,
                    paired=None  # BLE doesn't have traditional pairing concept
                ))
            return results

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                devices = loop.run_until_complete(scan())
            finally:
                loop.close()
        except Exception:
            return []

        # Cache devices for later connection
        for device in devices:
            self._device_cache[device.address] = device

        return devices

    def create_socket(self) -> SocketLike:
        """Create a BLE socket-like object for communication."""
        return _BleakSocket(self)

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        """BLE doesn't use RFCOMM channels, return a dummy value."""
        return 1

    def ensure_paired(self, address: str) -> None:
        """BLE pairing is handled automatically by CoreBluetooth.
        
        On macOS, pairing happens automatically when accessing protected
        characteristics. The OS will prompt the user if needed.
        """
        # No explicit pairing needed for BLE on macOS
        pass
