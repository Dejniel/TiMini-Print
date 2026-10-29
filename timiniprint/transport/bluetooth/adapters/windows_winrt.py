from __future__ import annotations

import asyncio
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Awaitable, Dict, List, Optional, Tuple, TypeVar

from ..constants import SPP_UUID
from ..types import DeviceInfo, DeviceTransport

T = TypeVar("T")
ScanResult = Tuple[List[DeviceInfo], Dict[str, str]]
_CLOSE_TIMEOUT_SEC = 2.0


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
        from winsdk.windows.storage.streams import DataReader, DataWriter, InputStreamOptions
    except Exception as exc:
        raise RuntimeError(_winrt_missing_message()) from exc
    return (DeviceInformation, DeviceInformationKind, RfcommDeviceService,
            RfcommServiceId, StreamSocket, DataWriter, DataReader, InputStreamOptions)


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
    DeviceInformation, *_ = _winrt_imports()
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
    DeviceInformation, DeviceInformationKind, RfcommDeviceService, RfcommServiceId, *_ = _winrt_imports()
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
        name = device.name or info.name or ""
        address = _format_bt_address(getattr(device, "bluetooth_address", 0))
        if not address:
            address = _extract_address_from_id(info.id) or info.id
        if address not in mapping:
            mapping[address] = info.id
        pairing = getattr(info, "pairing", None)
        is_paired = getattr(pairing, "is_paired", None) if pairing else None
        devices.append(
            DeviceInfo(
                name=name,
                address=address,
                paired=is_paired,
                transport=DeviceTransport.CLASSIC,
            )
        )
    return DeviceInfo.dedupe(devices), mapping


def _scan_winrt(timeout: float) -> ScanResult:
    return _run_winrt(_scan_winrt_async(timeout))


class _WinRtSocket:
    def __init__(self, backend: "_WinRtClassicBackend") -> None:
        self._backend = backend
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._socket = None
        self._writer = None
        self._reader = None
        self._read_task = None
        self._thread = None
        self._timeout = 12.0

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def gettimeout(self) -> float:
        return self._timeout

    def _start_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            ready.set()
            try:
                self._loop.run_forever()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=run, name="timiniprint-winrt-spp", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._loop.close()
            self._loop = None
            self._thread = None
            raise
        ready.wait()

    def _run(self, coro: Awaitable[T]) -> T:
        if self._loop is None or self._loop.is_closed() or not self._thread.is_alive():
            coro.close()
            raise RuntimeError("WinRT Bluetooth event loop is not running")
        pending = asyncio.run_coroutine_threadsafe(coro, self._loop)
        while not pending.done():
            try:
                return pending.result(timeout=0.1)
            except FutureTimeoutError:
                if pending.done():
                    raise
                if not self._thread.is_alive():
                    raise RuntimeError("WinRT Bluetooth event loop stopped unexpectedly")
        return pending.result()

    def connect(self, target) -> None:
        self.close()
        self._start_loop()
        address, _channel = target
        self._run(asyncio.wait_for(self._connect_async(address), timeout=self._timeout))

    async def _connect_async(self, address: str) -> None:
        service = await self._backend._resolve_service_async(address, timeout=self._timeout)
        if not service:
            raise RuntimeError("Bluetooth SPP service not found for device")
        _, _, _, _, StreamSocket, DataWriter, DataReader, InputStreamOptions = _winrt_imports()
        self._socket = StreamSocket()
        await self._socket.connect_async(service.connection_host_name, service.connection_service_name)
        self._writer = DataWriter(self._socket.output_stream)
        self._reader = DataReader(self._socket.input_stream)
        self._reader.input_stream_options = InputStreamOptions.PARTIAL

    def sendall(self, data: bytes) -> None:
        self._run(asyncio.wait_for(self._send_async(bytes(data)), timeout=self._timeout))

    async def _send_async(self, data: bytes) -> None:
        if not self._writer:
            raise RuntimeError("Not connected to a Bluetooth SPP device")
        self._writer.write_bytes(bytearray(data))
        await self._writer.store_async()
        await self._writer.flush_async()

    def start_receive(self, on_data, on_error) -> None:
        self._run(self._start_receive_async(on_data, on_error))

    async def _start_receive_async(self, on_data, on_error) -> None:
        if self._reader is None:
            raise RuntimeError("Not connected to a Bluetooth SPP device")
        if self._read_task is not None:
            raise RuntimeError("WinRT SPP receiver already installed")
        self._read_task = asyncio.create_task(self._receive_async(on_data, on_error))

    async def _receive_async(self, on_data, on_error) -> None:
        try:
            while True:
                count = await self._reader.load_async(4096)
                if count == 0:
                    raise ConnectionError("Bluetooth SPP peer closed its input stream")
                data = bytearray(count)
                self._reader.read_bytes(data)
                on_data(bytes(data))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            on_error(exc)

    def stop_receive(self) -> None:
        if self._read_task is not None:
            self._run(self._stop_receive_async())

    async def _stop_receive_async(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            done, _ = await asyncio.wait((self._read_task,), timeout=_CLOSE_TIMEOUT_SEC)
            if not done:
                raise TimeoutError("WinRT SPP input did not stop")
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

    def close(self) -> None:
        if self._loop is not None:
            if not self._thread.is_alive():
                if any(resource is not None for resource in (self._reader, self._writer, self._socket)):
                    raise RuntimeError("WinRT event loop stopped with live Bluetooth resources")
                self._loop = None
                self._thread = None
                return
            self._run(self._close_async())
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=_CLOSE_TIMEOUT_SEC)
            if self._thread.is_alive():
                raise RuntimeError("WinRT Bluetooth event loop did not stop")
            self._loop = None
            self._thread = None

    async def _close_async(self) -> None:
        await self._stop_receive_async()
        for field in ("_reader", "_writer", "_socket"):
            resource = getattr(self, field)
            if resource is not None:
                resource.close()
                setattr(self, field, None)


class _WinRtClassicBackend:
    def __init__(self) -> None:
        self._service_by_address: Dict[str, str] = {}

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        devices, mapping = _scan_winrt(timeout)
        self._service_by_address = mapping
        return devices

    def refresh_mapping(self, timeout: float) -> Dict[str, str]:
        _, mapping = _scan_winrt(timeout)
        self._service_by_address = mapping
        return mapping

    def has_service(self, address: str) -> bool:
        return address in self._service_by_address

    def get_service_id(self, address: str) -> Optional[str]:
        return self._service_by_address.get(address)

    def ensure_paired(self, address: str) -> None:
        service_id = self._service_by_address.get(address)
        _run_winrt(_pair_winrt_async(address, service_id))

    def create_socket(self) -> _WinRtSocket:
        return _WinRtSocket(self)

    async def _resolve_service_async(self, address: str, timeout: float = 5.0):
        service_id = self._service_by_address.get(address)
        if not service_id:
            _, mapping = await _scan_winrt_async(timeout)
            self._service_by_address = mapping
            service_id = self._service_by_address.get(address)
        if not service_id:
            return None
        _, _, RfcommDeviceService, *_ = _winrt_imports()
        return await RfcommDeviceService.from_id_async(service_id)
