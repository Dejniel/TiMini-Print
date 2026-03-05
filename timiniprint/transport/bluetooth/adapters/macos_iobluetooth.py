from __future__ import annotations

import importlib
import time
from typing import Dict, Iterable, List, Optional

from ..constants import SPP_UUID
from ..types import DeviceInfo, DeviceTransport

_SPP_UUID_16 = 0x1101


def _missing_iobluetooth_message() -> str:
    return (
        "Classic Bluetooth on macOS requires PyObjC IOBluetooth bindings. "
        "Install with: pip install -r requirements.txt"
    )


def _iobluetooth_imports():
    try:
        objc = importlib.import_module("objc")
        iobluetooth = importlib.import_module("IOBluetooth")
    except Exception as exc:
        raise RuntimeError(_missing_iobluetooth_message()) from exc
    return objc, iobluetooth


def _normalize_address(value: str) -> str:
    text = value.strip().replace("-", ":").upper()
    parts = text.split(":")
    if len(parts) != 6:
        return text
    normalized_parts = []
    for part in parts:
        if not part:
            return text
        normalized_parts.append(part.zfill(2)[-2:])
    return ":".join(normalized_parts)


def _status_ok(status) -> bool:
    if status is None:
        return True
    if isinstance(status, bool):
        return status
    if isinstance(status, int):
        return status == 0
    return False


def _extract_status(result):
    if isinstance(result, tuple) and result:
        head = result[0]
        if head is None or isinstance(head, (int, bool)):
            return head
        return None
    if result is None or isinstance(result, (int, bool)):
        return result
    return None


def _extract_channel(result):
    if hasattr(result, "writeSync_length_"):
        return result
    if not isinstance(result, tuple):
        return None
    tail = result[1:] if result and isinstance(result[0], (type(None), int, bool)) else result
    for item in tail:
        if hasattr(item, "writeSync_length_"):
            return item
    return None


def _device_name(device) -> str:
    try:
        name = device.nameOrAddress()
    except Exception:
        name = ""
    if name is None:
        return ""
    return str(name).strip()


def _device_address(device) -> str:
    try:
        address = device.addressString()
    except Exception:
        address = ""
    if address is None:
        return ""
    return _normalize_address(str(address))


def _device_is_paired(device) -> bool:
    try:
        return bool(device.isPaired())
    except Exception:
        return False


def _device_to_info(device) -> Optional[DeviceInfo]:
    address = _device_address(device)
    if not address:
        return None
    return DeviceInfo(
        name=_device_name(device),
        address=address,
        paired=_device_is_paired(device),
        transport=DeviceTransport.CLASSIC,
    )


def _wait_until_not_running(inquiry, timeout: float) -> None:
    deadline = time.monotonic() + max(1.0, timeout + 0.5)
    while time.monotonic() < deadline:
        try:
            if not bool(inquiry.isInquiryRunning()):
                return
        except Exception:
            return
        time.sleep(0.05)


def _scan_inquiry_raw(timeout: float) -> List[object]:
    _, iobluetooth = _iobluetooth_imports()
    inquiry = iobluetooth.IOBluetoothDeviceInquiry.inquiryWithDelegate_(None)
    if inquiry is None:
        raise RuntimeError("Unable to create IOBluetooth inquiry object")

    inquiry.setInquiryLength_(max(1, int(round(timeout))))
    inquiry.setUpdateNewDeviceNames_(True)

    status = inquiry.start()
    if not _status_ok(status):
        raise RuntimeError(f"IOBluetooth inquiry start failed (status: {status})")

    _wait_until_not_running(inquiry, timeout)

    try:
        inquiry.stop()
    except Exception:
        pass

    devices = inquiry.foundDevices()
    if devices is None:
        return []
    return list(devices)


def _scan_paired_raw() -> List[object]:
    _, iobluetooth = _iobluetooth_imports()
    devices = iobluetooth.IOBluetoothDevice.pairedDevices()
    if devices is None:
        return []
    return list(devices)


def _device_by_address(address: str):
    normalized = _normalize_address(address)
    _, iobluetooth = _iobluetooth_imports()
    device = iobluetooth.IOBluetoothDevice.deviceWithAddressString_(normalized)
    if device:
        return device
    return iobluetooth.IOBluetoothDevice.deviceWithAddressString_(normalized.replace(":", "-"))


def _find_device_in_list(devices: Iterable[object], address: str):
    target = _normalize_address(address)
    for device in devices:
        if _device_address(device) == target:
            return device
    return None


def _build_spp_uuid():
    _, iobluetooth = _iobluetooth_imports()
    uuid = iobluetooth.IOBluetoothSDPUUID.uuid16_(_SPP_UUID_16)
    if uuid is None:
        raise RuntimeError(f"Failed to build SPP UUID object for {SPP_UUID}")
    return uuid


def _extract_channel_id(result) -> Optional[int]:
    if isinstance(result, int):
        return result if result > 0 else None
    if not isinstance(result, tuple):
        return None

    status = _extract_status(result)
    if not _status_ok(status):
        return None

    for item in result[1:]:
        if isinstance(item, int) and item > 0:
            return item
    return None


def _service_channel_id(service) -> Optional[int]:
    try:
        result = service.getRFCOMMChannelID_(None)
    except Exception:
        return None
    return _extract_channel_id(result)


def _resolve_rfcomm_channels_via_services(device) -> List[int]:
    try:
        services = device.services()
    except Exception:
        services = None
    if services is None:
        return []

    channels: List[int] = []
    for service in list(services):
        channel_id = _service_channel_id(service)
        if channel_id is not None and channel_id not in channels:
            channels.append(channel_id)
    return channels


def _find_spp_channels(device) -> List[int]:
    uuid = _build_spp_uuid()
    channels: List[int] = []

    service = device.getServiceRecordForUUID_(uuid)
    if service:
        channel_id = _service_channel_id(service)
        if channel_id is not None and channel_id not in channels:
            channels.append(channel_id)

    # If SPP record is not cached yet, refresh SDP once and retry.
    status = device.performSDPQuery_(None)
    if _status_ok(status):
        service = device.getServiceRecordForUUID_(uuid)
        if service:
            channel_id = _service_channel_id(service)
            if channel_id is not None and channel_id not in channels:
                channels.append(channel_id)

    for channel_id in _resolve_rfcomm_channels_via_services(device):
        if channel_id not in channels:
            channels.append(channel_id)
    return channels


def _attempt_pair_with_device_pair(device) -> bool:
    if _device_is_paired(device):
        return True

    _, iobluetooth = _iobluetooth_imports()
    pair = iobluetooth.IOBluetoothDevicePair.pairWithDevice_(device)
    if pair is None:
        raise RuntimeError("IOBluetoothDevicePair returned no pairing object")

    status = pair.start()
    if not _status_ok(status):
        raise RuntimeError(f"IOBluetooth pairing failed to start (status: {status})")

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if _device_is_paired(device):
            return True
        time.sleep(0.1)
    return _device_is_paired(device)


def _open_rfcomm_channel(device, channel_id: int):
    result = device.openRFCOMMChannelSync_withChannelID_delegate_(None, int(channel_id), None)
    status = _extract_status(result)
    if not _status_ok(status):
        raise RuntimeError(f"Failed to open RFCOMM channel {channel_id} (status: {status})")

    channel = _extract_channel(result)
    if channel is None:
        raise RuntimeError("IOBluetooth did not return an RFCOMM channel object")
    return channel


def _rfcomm_write(channel, data: bytes) -> None:
    payload = bytearray(data)
    result = channel.writeSync_length_(payload, len(payload))
    status = _extract_status(result)
    if not _status_ok(status):
        raise RuntimeError(f"RFCOMM write failed (status: {status})")


def _rfcomm_close(channel) -> None:
    try:
        channel.closeChannel()
    except Exception:
        pass


def _close_device_connection(device) -> None:
    try:
        device.closeConnection()
    except Exception:
        pass


class _MacClassicSocket:
    def __init__(self, backend: "_MacClassicBackend") -> None:
        self._backend = backend
        self._timeout: Optional[float] = None
        self._device = None
        self._channel = None

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def connect(self, target) -> None:
        address, channel_id = target
        timeout = self._timeout if self._timeout is not None else 5.0
        # Ensure stale channel/device handles never leak across reconnects.
        self.close()
        device = self._backend.get_device(address, allow_discovery=True, timeout=timeout)
        if device is None:
            raise RuntimeError(f"Bluetooth device not found: {address}")
        self._device = device
        try:
            self._channel = _open_rfcomm_channel(device, int(channel_id))
        except Exception:
            _close_device_connection(device)
            self._device = None
            self._channel = None
            raise

    def sendall(self, data: bytes) -> None:
        if self._channel is None:
            raise RuntimeError("Not connected to a Bluetooth RFCOMM channel")
        _rfcomm_write(self._channel, data)

    def close(self) -> None:
        if self._channel is not None:
            _rfcomm_close(self._channel)
            self._channel = None
        if self._device is not None:
            _close_device_connection(self._device)
            self._device = None


class _MacClassicBackend:
    def __init__(self) -> None:
        self._devices_by_address: Dict[str, object] = {}

    def _remember_devices(self, devices: Iterable[object]) -> List[DeviceInfo]:
        infos: List[DeviceInfo] = []
        for device in devices:
            info = _device_to_info(device)
            if info is None:
                continue
            infos.append(info)
            self._devices_by_address[info.address] = device
        return infos

    def scan_inquiry(self, timeout: float) -> List[DeviceInfo]:
        inquiry_error = None
        try:
            inquiry_infos = self._remember_devices(_scan_inquiry_raw(timeout))
        except Exception as exc:
            inquiry_infos = []
            inquiry_error = exc

        if inquiry_infos:
            return DeviceInfo.dedupe(inquiry_infos)

        try:
            paired_infos = self._remember_devices(_scan_paired_raw())
        except Exception as exc:
            if inquiry_error is not None:
                raise RuntimeError(f"macOS Classic Bluetooth scan failed: {inquiry_error}") from inquiry_error
            raise RuntimeError(f"macOS Classic Bluetooth scan failed: {exc}") from exc

        if paired_infos:
            return DeviceInfo.dedupe(paired_infos)

        if inquiry_error is not None:
            raise RuntimeError(f"macOS Classic Bluetooth scan failed: {inquiry_error}") from inquiry_error
        return []

    def resolve_rfcomm_channels(self, address: str) -> List[int]:
        device = self.get_device(address, allow_discovery=True, timeout=5.0)
        if device is None:
            return []
        try:
            return _find_spp_channels(device)
        except Exception:
            return []

    def pair_device(self, address: str) -> bool:
        device = self.get_device(address, allow_discovery=True, timeout=8.0)
        if device is None:
            raise RuntimeError(f"Bluetooth device not found: {address}")

        if _device_is_paired(device):
            return True

        if _attempt_pair_with_device_pair(device):
            return True

        if _device_is_paired(device):
            return True

        raise RuntimeError(f"Automatic pairing failed for {address}")

    def create_socket(self) -> _MacClassicSocket:
        return _MacClassicSocket(self)

    def get_device(self, address: str, *, allow_discovery: bool, timeout: float):
        normalized = _normalize_address(address)

        cached = self._devices_by_address.get(normalized)
        if cached is not None:
            return cached

        direct = _device_by_address(normalized)
        if direct is not None:
            self._devices_by_address[normalized] = direct
            return direct

        paired = _scan_paired_raw()
        paired_match = _find_device_in_list(paired, normalized)
        if paired_match is not None:
            self._remember_devices(paired)
            return paired_match

        if allow_discovery:
            discovered = _scan_inquiry_raw(timeout)
            discovered_match = _find_device_in_list(discovered, normalized)
            if discovered_match is not None:
                self._remember_devices(discovered)
                return discovered_match

        return None
