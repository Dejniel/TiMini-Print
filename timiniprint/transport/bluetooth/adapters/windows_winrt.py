from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Dict, List, Optional, Tuple, TypeVar, TYPE_CHECKING

from ..constants import SPP_UUID
from ..types import DeviceInfo

if TYPE_CHECKING:
    from .windows_adapter import _WindowsBluetoothAdapter

T = TypeVar("T")
ScanResult = Tuple[List[DeviceInfo], Dict[str, str]]


def _winrt_missing_message() -> str:
    return (
        "WinRT Bluetooth support on Windows requires the 'winsdk' package. "
        "Install with: pip install -r requirements.txt"
    )


def _winrt_imports():
    try:
        from winsdk.windows.devices.bluetooth.rfcomm import RfcommDeviceService, RfcommServiceId
        from winsdk.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
        from winsdk.windows.networking.sockets import StreamSocket
        from winsdk.windows.storage.streams import DataWriter
    except Exception as exc:
        raise RuntimeError(_winrt_missing_message()) from exc
    return DeviceInformation, DeviceInformationKind, RfcommDeviceService, RfcommServiceId, StreamSocket, DataWriter


def _run_winrt(coro: Awaitable[T]) -> T:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            asyncio.set_event_loop(None)
        finally:
            loop.close()


def _format_bt_address(value: int) -> str:
    if not value:
        return ""
    text = f"{value:012X}"
    return ":".join(text[i : i + 2] for i in range(0, 12, 2))


def _parse_bt_address(address: str) -> Optional[int]:
    cleaned = address.replace(":", "").replace("-", "")
    if len(cleaned) != 12:
        return None
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


_ADDRESS_RE = re.compile(r"([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})")


def _extract_address_from_id(device_id: str) -> str:
    if not device_id:
        return ""
    match = _ADDRESS_RE.search(device_id)
    if not match:
        return ""
    return match.group(1).replace("-", ":").upper()


async def _pair_device_info_async(info) -> None:
    pairing = getattr(info, "pairing", None)
    if not pairing:
        raise RuntimeError("pairing is not supported for this device")
    if getattr(pairing, "is_paired", False):
        return
    result = await pairing.pair_async()
    if getattr(pairing, "is_paired", False):
        return
    status = getattr(result, "status", None)
    status_text = getattr(status, "name", None) or str(status)
    if status_text and status_text.lower().replace(" ", "_") in {"paired", "already_paired", "alreadypaired"}:
        return
    raise RuntimeError(f"pairing failed (status: {status_text})")


async def _pair_winrt_async(address: str, service_id: Optional[str]) -> None:
    DeviceInformation, _, _, _, _, _ = _winrt_imports()
    last_error = None

    if service_id:
        try:
            info = await DeviceInformation.create_from_id_async(service_id)
        except Exception:
            info = None
        if info:
            try:
                await _pair_device_info_async(info)
                return
            except Exception as exc:
                last_error = exc

    try:
        from winsdk.windows.devices.bluetooth import BluetoothDevice
    except Exception:
        BluetoothDevice = None
    if BluetoothDevice:
        addr_value = _parse_bt_address(address)
        if addr_value is not None:
            device = await BluetoothDevice.from_bluetooth_address_async(addr_value)
            info = getattr(device, "device_information", None)
            if info:
                try:
                    await _pair_device_info_async(info)
                    return
                except Exception as exc:
                    last_error = exc
    if last_error:
        raise RuntimeError(f"pairing failed: {last_error}")
    raise RuntimeError("pairing is not available for this device")


async def _scan_winrt_async(timeout: float) -> ScanResult:
    DeviceInformation, DeviceInformationKind, RfcommDeviceService, RfcommServiceId, _, _ = _winrt_imports()
    selector = str(RfcommDeviceService.get_device_selector(RfcommServiceId.from_uuid(SPP_UUID)))

    async def find_all():
        try:
            return await DeviceInformation.find_all_async(selector)
        except TypeError:
            return await DeviceInformation.find_all_async(
                selector, [], DeviceInformationKind.ASSOCIATION_ENDPOINT
            )

    if timeout:
        infos = await asyncio.wait_for(find_all(), timeout=timeout)
    else:
        infos = await find_all()
    devices: List[DeviceInfo] = []
    mapping: Dict[str, str] = {}
    for info in infos:
        service = await RfcommDeviceService.from_id_async(info.id)
        if not service:
            continue
        device = service.device
        name = (device.name or info.name or "").strip()
        address = _format_bt_address(getattr(device, "bluetooth_address", 0))
        if not address:
            address = _extract_address_from_id(info.id) or info.id
        if address not in mapping:
            mapping[address] = info.id
        pairing = getattr(info, "pairing", None)
        is_paired = getattr(pairing, "is_paired", None) if pairing else None
        devices.append(DeviceInfo(name=name, address=address, paired=is_paired))
    return DeviceInfo.dedupe(devices), mapping


def _scan_winrt(timeout: float) -> ScanResult:
    return _run_winrt(_scan_winrt_async(timeout))


class _WinRtSocket:
    def __init__(self, adapter: "_WindowsBluetoothAdapter") -> None:
        self._adapter = adapter
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._socket = None
        self._writer = None

    def _run(self, coro: Awaitable[T]) -> T:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(coro)

    def connect(self, target) -> None:
        address, _channel = target
        service = self._run(self._adapter._resolve_service_async(address))
        if not service:
            raise RuntimeError("Bluetooth SPP service not found for device")
        _, _, _, _, StreamSocket, DataWriter = _winrt_imports()
        self._socket = StreamSocket()
        self._run(self._socket.connect_async(service.connection_host_name, service.connection_service_name))
        self._writer = DataWriter(self._socket.output_stream)

    def sendall(self, data: bytes) -> None:
        if not self._writer:
            raise RuntimeError("Not connected to a Bluetooth SPP device")
        self._writer.write_bytes(bytearray(data))
        self._run(self._writer.store_async())
        self._run(self._writer.flush_async())

    def close(self) -> None:
        if self._writer:
            close_writer = getattr(self._writer, "close", None)
            if callable(close_writer):
                close_writer()
            self._writer = None
        if self._socket:
            close_socket = getattr(self._socket, "close", None)
            if callable(close_socket):
                close_socket()
            self._socket = None
        if self._loop:
            asyncio.set_event_loop(None)
            self._loop.close()
            self._loop = None
