"""Linux-only direct ATT client used as a BlueZ Device1.Connect workaround.

Some BLE-only printers advertise a misleading dual-mode BR/EDR + LE controller
flag. On Linux with BlueZ 5.83, Bleak 3.0.2 still connects through
``org.bluez.Device1.Connect()``, and BlueZ can choose BR/EDR internally before
GATT is available. The result is a timeout even though direct LE ATT/L2CAP works.

BlueZ 5.86 adds bearer-specific connection APIs such as
``org.bluez.Bearer.LE1.Connect()``, but that is not a practical baseline for
current users yet and Bleak does not expose a stable public "force LE bearer"
option for this path.

Bundling a newer BlueZ with the release binary is intentionally not treated as
the fix: BlueZ is a system daemon tied to the kernel Bluetooth stack, D-Bus, and
host permissions, not a regular user-space library we can safely ship beside the
executable.

This module intentionally bypasses ``Device1.Connect()`` and opens the ATT fixed
channel directly. Keep it isolated so it can be removed when the supported Linux
stack has a reliable LE-bearer connect path exposed through Bleak, e.g. a common
stable BlueZ/Bleak combination that can force LE without raw socket handling
here.
"""
from __future__ import annotations

import asyncio
import ctypes
import errno as errno_module
import select
import socket
import struct
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional

from .base import _BleBluetoothAdapter
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from .bleak_adapter_transport import _BleakTransportSession
from ..types import DeviceInfo, SocketLike
from .... import reporting
from ....protocol.families import get_protocol_behavior
from ....protocol.family import ProtocolFamily

_AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
_BTPROTO_L2CAP = getattr(socket, "BTPROTO_L2CAP", 0)
_SOL_BLUETOOTH = 274
_BT_SECURITY = 4
_BT_SECURITY_LOW = 1
_L2CAP_CID_ATT = 4
_BDADDR_LE_PUBLIC = 1
_BDADDR_LE_RANDOM = 2
_ATT_DEFAULT_MTU = 23
_ATT_CLIENT_MTU = 512
_ATT_HANDLE_MAX = 0xFFFF
_ATT_ERR_ATTR_NOT_FOUND = 0x0A
_ATT_OP_ERROR = 0x01
_ATT_OP_MTU_REQ = 0x02
_ATT_OP_MTU_RESP = 0x03
_ATT_OP_FIND_INFO_REQ = 0x04
_ATT_OP_FIND_INFO_RESP = 0x05
_ATT_OP_READ_BY_TYPE_REQ = 0x08
_ATT_OP_READ_BY_TYPE_RESP = 0x09
_ATT_OP_READ_BY_GROUP_TYPE_REQ = 0x10
_ATT_OP_READ_BY_GROUP_TYPE_RESP = 0x11
_ATT_OP_WRITE_REQ = 0x12
_ATT_OP_WRITE_RESP = 0x13
_ATT_OP_NOTIFY = 0x1B
_ATT_OP_INDICATE = 0x1D
_ATT_OP_CONFIRM = 0x1E
_ATT_OP_WRITE_CMD = 0x52
_UUID_PRIMARY_SERVICE = 0x2800
_UUID_CHARACTERISTIC = 0x2803
_UUID_CCCD = 0x2902
_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


class _LinuxAttAdapter(_BleBluetoothAdapter):
    """BLE adapter using the isolated Linux direct ATT workaround."""

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        _ = timeout
        return []

    def create_socket(
        self,
        pairing_hint: Optional[bool] = None,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> SocketLike:
        _ = pairing_hint
        return _LinuxAttSocket(
            protocol_family=protocol_family,
            reporter=reporter,
        )

    def ensure_paired(self, address: str, pairing_hint: Optional[bool] = None) -> None:
        return None


@dataclass
class _LinuxAttDescriptor:
    handle: int
    uuid: str


@dataclass
class _LinuxAttCharacteristic:
    uuid: str
    handle: int
    declaration_handle: int
    properties_mask: int
    properties: tuple[str, ...]
    descriptors: list[_LinuxAttDescriptor] = field(default_factory=list)
    max_write_without_response_size: int = _ATT_DEFAULT_MTU - 3


@dataclass
class _LinuxAttService:
    uuid: str
    start_handle: int
    end_handle: int
    characteristics: list[_LinuxAttCharacteristic] = field(default_factory=list)


class _SockaddrL2(ctypes.Structure):
    _fields_ = [
        ("l2_family", ctypes.c_ushort),
        ("l2_psm", ctypes.c_ushort),
        ("l2_bdaddr", ctypes.c_ubyte * 6),
        ("l2_cid", ctypes.c_ushort),
        ("l2_bdaddr_type", ctypes.c_ubyte),
    ]


class _LinuxAttSocket:
    """BLE socket surface backed by Linux direct ATT and shared transport routing."""

    def __init__(
        self,
        *,
        protocol_family: Optional[ProtocolFamily] = None,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> None:
        self._protocol_family = ProtocolFamily.from_value(protocol_family)
        self._reporter = reporter
        self._client: _LinuxAttClient | None = None
        self._connected = False
        self._mtu_size = _ATT_DEFAULT_MTU - 3
        self._timeout = 30.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._write_resolver = _BleWriteEndpointResolver(reporter=self._reporter)
        self._transport = self._new_transport_session()

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout
        if self._client is not None:
            self._client.settimeout(timeout)

    def connect(self, address: str) -> None:
        self._client = _LinuxAttClient(reporter=self._reporter)
        try:
            self._loop = asyncio.new_event_loop()
            self._client.settimeout(self._timeout)
            self._client.connect(address)
            self._connected = True
            self._mtu_size = min(max(1, self._client.mtu_size - 3), 512)
            transport = get_protocol_behavior(self._protocol_family).transport
            selection = self._write_resolver.resolve(
                self._client.services,
                preferred_service_uuid=transport.preferred_service_uuid,
                preferred_write_char_uuid=transport.preferred_write_char_uuid,
            )
            if not selection:
                raise RuntimeError(
                    f"Could not find a writable GATT characteristic on device {address}."
                )
            self._transport.apply_write_selection(selection)
            self._transport.configure_endpoints(self._client.services)
            self._run(self._transport.start_notify_if_available(self._client, self._handle_notification))
            self._run(
                self._transport.initialize_connection(
                    self._client,
                    mtu_size=self._mtu_size,
                    timeout=self._timeout,
                )
            )
        except Exception:
            self.close()
            raise

    def send(self, data: bytes) -> int:
        return self.send_payload(data)

    def sendall(self, data: bytes) -> None:
        self.send_payload(data)

    def send_payload(self, data: bytes, runtime_controller=None) -> int:
        if not self._connected or self._client is None:
            raise RuntimeError("Not connected to BLE device")
        self._run(
            self._transport.send(
                self._client,
                data,
                mtu_size=self._mtu_size,
                timeout=self._timeout,
                runtime_controller=runtime_controller,
            )
        )
        return len(data)

    def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0):
        if not self._connected or self._client is None:
            raise RuntimeError("Not connected to BLE device")
        self._run(
            self._transport.attach_runtime_controller(
                runtime_controller,
                mtu_size=self._mtu_size,
                timeout=timeout,
            )
        )

    def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        if not self._connected or self._client is None:
            return False
        return bool(self._run(self._transport.send_control_packet(packet, timeout=timeout)))

    def can_send_control_packet(self) -> bool:
        if not self._connected or self._client is None:
            return False
        return self._transport.can_send_control_packet()

    def can_query_control_packet(self) -> bool:
        if not self._connected or self._client is None:
            return False
        return self._transport.can_query_control_packet()

    def can_wait_for_notification(self) -> bool:
        if not self._connected or self._client is None:
            return False
        return self._transport.can_wait_for_notification()

    def can_send_control_packet_wait_notification(self) -> bool:
        if not self._connected or self._client is None:
            return False
        return self._transport.can_send_control_packet_wait_notification()

    def query_control_packet(self, packet: bytes, *, timeout: float = 1.0, reply_complete=None) -> bytes | None:
        if not self._connected or self._client is None:
            return None
        return self._run(
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
        if not self._connected or self._client is None:
            if required:
                raise RuntimeError("Not connected to BLE device")
            return None
        return self._run(
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
        if not self._connected or self._client is None:
            if required:
                raise RuntimeError("Not connected to BLE device")
            return None
        return self._run(
            self._transport.send_control_packet_wait_notification(
                packet,
                label=label,
                match=match,
                timeout=timeout,
                required=required,
            )
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._transport.report_disconnect_diagnostics()
            except Exception:
                pass
            try:
                self._run(self._transport.stop_notify_if_started(self._client))
            except Exception:
                pass
            self._client.close()
        self._client = None
        self._connected = False
        self._transport = self._new_transport_session()
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    def _new_transport_session(self) -> _BleakTransportSession:
        return _BleakTransportSession(
            protocol_family=self._protocol_family,
            transport_profile=get_protocol_behavior(self._protocol_family).transport,
            write_resolver=self._write_resolver,
            reporter=self._reporter,
        )

    def _handle_notification(self, _sender: Any, data: Any) -> None:
        payload = bytes(data)
        loop = self._loop
        if loop is None or loop.is_closed():
            self._transport.handle_notification(payload)
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            self._transport.handle_notification(payload)
            return
        loop.call_soon_threadsafe(self._transport.handle_notification, payload)

    def _run(self, coro):
        if self._loop is None:
            raise RuntimeError("Event loop not initialized")
        previous_loop = None
        try:
            try:
                previous_loop = asyncio.get_event_loop()
            except RuntimeError:
                previous_loop = None
            asyncio.set_event_loop(self._loop)
            return self._loop.run_until_complete(coro)
        finally:
            try:
                asyncio.set_event_loop(previous_loop)
            except Exception:
                pass


class _LinuxAttClient:
    """Minimal synchronous Linux ATT/GATT client."""

    def __init__(
        self,
        *,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> None:
        self._reporter = reporter
        self._sock: socket.socket | None = None
        self._timeout = 30.0
        self._mtu = _ATT_DEFAULT_MTU
        self.services: list[_LinuxAttService] = []
        self.mtu_size = _ATT_DEFAULT_MTU
        self._notify_callbacks: dict[int, Callable[[Any, bytes], None]] = {}
        self._response_queue: list[bytes] = []
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._running = False

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout
        if self._sock is not None:
            self._sock.settimeout(timeout)

    def connect(self, address) -> None:
        # The shared adapter-fallback wrapper passes the same address/channel
        # tuple it iterates over to every backend, including this one.
        # The RFCOMM channel is meaningless for an LE L2CAP/ATT socket — but
        # we still have to accept the tuple form so we don't blow up before
        # we get to the actual connect.
        if isinstance(address, tuple):
            address = address[0]
        self._reporter.debug(short="BLE", detail=f"Linux direct ATT connect: address={address}")
        last_error: Exception | None = None
        for address_type in (_BDADDR_LE_PUBLIC, _BDADDR_LE_RANDOM):
            try:
                self._sock = _open_att_socket(address, address_type, timeout=self._timeout)
                self._exchange_mtu()
                self.services = self._discover_services()
                self._update_characteristic_mtu()
                self._reporter.debug(
                    short="BLE",
                    detail=(
                        "Linux direct ATT connected: "
                        f"address={address} address_type={address_type} mtu_payload={self._mtu - 3} "
                        f"services={len(self.services)}"
                    ),
                )
                return
            except Exception as exc:
                last_error = exc
                self.close()
        raise RuntimeError(f"direct ATT connect failed: {last_error}")

    def close(self) -> None:
        self._running = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        with self._condition:
            self._condition.notify_all()
        thread = self._rx_thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=0.5)
            except Exception:
                pass
        self._rx_thread = None
        self._notify_callbacks.clear()
        self._response_queue.clear()

    async def write_gatt_char(self, char: Any, data: bytes, response: bool = False) -> None:
        characteristic = self._resolve_characteristic(char)
        if characteristic is None:
            raise RuntimeError(f"ATT characteristic not found: {char}")
        handle = characteristic.handle
        if response:
            self._request(
                bytes([_ATT_OP_WRITE_REQ]) + struct.pack("<H", handle) + data,
                expected_opcodes={_ATT_OP_WRITE_RESP},
                request_opcode=_ATT_OP_WRITE_REQ,
                timeout=self._timeout,
            )
            return
        self._send_pdu(bytes([_ATT_OP_WRITE_CMD]) + struct.pack("<H", handle) + data)

    async def start_notify(self, char_uuid: str, callback) -> None:
        characteristic = self._resolve_characteristic(char_uuid)
        if characteristic is None:
            raise RuntimeError(f"ATT notify characteristic not found: {char_uuid}")
        cccd = self._find_cccd(characteristic)
        if cccd is None:
            raise RuntimeError(f"CCCD descriptor not found for {characteristic.uuid}")
        value = b"\x01\x00" if "notify" in characteristic.properties else b"\x02\x00"
        self._request(
            bytes([_ATT_OP_WRITE_REQ]) + struct.pack("<H", cccd.handle) + value,
            expected_opcodes={_ATT_OP_WRITE_RESP},
            request_opcode=_ATT_OP_WRITE_REQ,
            timeout=self._timeout,
        )
        self._notify_callbacks[characteristic.handle] = callback
        self._start_rx_thread()

    async def stop_notify(self, char_uuid: str) -> None:
        characteristic = self._resolve_characteristic(char_uuid)
        if characteristic is None:
            return
        self._notify_callbacks.pop(characteristic.handle, None)
        cccd = self._find_cccd(characteristic)
        if cccd is None:
            return
        try:
            self._request(
                bytes([_ATT_OP_WRITE_REQ]) + struct.pack("<H", cccd.handle) + b"\x00\x00",
                expected_opcodes={_ATT_OP_WRITE_RESP},
                request_opcode=_ATT_OP_WRITE_REQ,
                timeout=min(self._timeout, 1.0),
            )
        except Exception:
            pass

    def _exchange_mtu(self) -> None:
        response = self._request(
            bytes([_ATT_OP_MTU_REQ]) + struct.pack("<H", _ATT_CLIENT_MTU),
            expected_opcodes={_ATT_OP_MTU_RESP},
            request_opcode=_ATT_OP_MTU_REQ,
            timeout=self._timeout,
        )
        if response and len(response) >= 3:
            server_mtu = struct.unpack_from("<H", response, 1)[0]
            self._mtu = max(_ATT_DEFAULT_MTU, min(_ATT_CLIENT_MTU, server_mtu))
            self.mtu_size = self._mtu

    def _discover_services(self) -> list[_LinuxAttService]:
        services: list[_LinuxAttService] = []
        start = 1
        while start <= _ATT_HANDLE_MAX:
            response = self._request(
                bytes([_ATT_OP_READ_BY_GROUP_TYPE_REQ])
                + struct.pack("<HHH", start, _ATT_HANDLE_MAX, _UUID_PRIMARY_SERVICE),
                expected_opcodes={_ATT_OP_READ_BY_GROUP_TYPE_RESP},
                request_opcode=_ATT_OP_READ_BY_GROUP_TYPE_REQ,
                timeout=self._timeout,
                allow_attr_not_found=True,
            )
            if response is None:
                break
            record_len = response[1]
            records = response[2:]
            if record_len not in {6, 20} or not records:
                break
            last_end = start
            for offset in range(0, len(records), record_len):
                record = records[offset : offset + record_len]
                if len(record) != record_len:
                    continue
                service_start, service_end = struct.unpack_from("<HH", record, 0)
                service_uuid = _decode_uuid(record[4:])
                services.append(_LinuxAttService(service_uuid, service_start, service_end))
                last_end = service_end
            start = last_end + 1
        for service in services:
            self._discover_characteristics(service)
            self._discover_descriptors(service)
        return services

    def _discover_characteristics(self, service: _LinuxAttService) -> None:
        start = service.start_handle
        found: list[_LinuxAttCharacteristic] = []
        while start <= service.end_handle:
            response = self._request(
                bytes([_ATT_OP_READ_BY_TYPE_REQ])
                + struct.pack("<HHH", start, service.end_handle, _UUID_CHARACTERISTIC),
                expected_opcodes={_ATT_OP_READ_BY_TYPE_RESP},
                request_opcode=_ATT_OP_READ_BY_TYPE_REQ,
                timeout=self._timeout,
                allow_attr_not_found=True,
            )
            if response is None:
                break
            record_len = response[1]
            records = response[2:]
            if record_len not in {7, 21} or not records:
                break
            last_decl = start
            for offset in range(0, len(records), record_len):
                record = records[offset : offset + record_len]
                if len(record) != record_len:
                    continue
                declaration_handle = struct.unpack_from("<H", record, 0)[0]
                properties_mask = record[2]
                value_handle = struct.unpack_from("<H", record, 3)[0]
                char_uuid = _decode_uuid(record[5:])
                found.append(
                    _LinuxAttCharacteristic(
                        uuid=char_uuid,
                        handle=value_handle,
                        declaration_handle=declaration_handle,
                        properties_mask=properties_mask,
                        properties=_properties_from_mask(properties_mask),
                    )
                )
                last_decl = declaration_handle
            start = last_decl + 1
        service.characteristics = found

    def _discover_descriptors(self, service: _LinuxAttService) -> None:
        characteristics = sorted(service.characteristics, key=lambda item: item.declaration_handle)
        for index, characteristic in enumerate(characteristics):
            next_decl = (
                characteristics[index + 1].declaration_handle
                if index + 1 < len(characteristics)
                else service.end_handle + 1
            )
            start = characteristic.handle + 1
            end = next_decl - 1
            if start > end:
                continue
            characteristic.descriptors.extend(self._find_descriptors(start, end))

    def _find_descriptors(self, start: int, end: int) -> list[_LinuxAttDescriptor]:
        descriptors: list[_LinuxAttDescriptor] = []
        cursor = start
        while cursor <= end:
            response = self._request(
                bytes([_ATT_OP_FIND_INFO_REQ]) + struct.pack("<HH", cursor, end),
                expected_opcodes={_ATT_OP_FIND_INFO_RESP},
                request_opcode=_ATT_OP_FIND_INFO_REQ,
                timeout=self._timeout,
                allow_attr_not_found=True,
            )
            if response is None:
                break
            fmt = response[1]
            record_len = 4 if fmt == 1 else 18 if fmt == 2 else 0
            if not record_len:
                break
            records = response[2:]
            last_handle = cursor
            for offset in range(0, len(records), record_len):
                record = records[offset : offset + record_len]
                if len(record) != record_len:
                    continue
                handle = struct.unpack_from("<H", record, 0)[0]
                descriptors.append(_LinuxAttDescriptor(handle=handle, uuid=_decode_uuid(record[2:])))
                last_handle = handle
            cursor = last_handle + 1
        return descriptors

    def _request(
        self,
        pdu: bytes,
        *,
        expected_opcodes: set[int],
        request_opcode: int,
        timeout: float,
        allow_attr_not_found: bool = False,
    ) -> bytes | None:
        self._send_pdu(pdu)
        if self._rx_thread is not None:
            return self._wait_for_response(
                expected_opcodes=expected_opcodes,
                request_opcode=request_opcode,
                timeout=timeout,
                allow_attr_not_found=allow_attr_not_found,
            )
        deadline = threading.Event()
        end_at = _monotonic_seconds() + max(0.0, timeout)
        while not deadline.is_set():
            remaining = end_at - _monotonic_seconds()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for ATT response")
            pdu = self._recv_pdu(remaining)
            result = self._handle_response_pdu(
                pdu,
                expected_opcodes=expected_opcodes,
                request_opcode=request_opcode,
                allow_attr_not_found=allow_attr_not_found,
            )
            if result is not _PENDING:
                return result
        return None

    def _wait_for_response(
        self,
        *,
        expected_opcodes: set[int],
        request_opcode: int,
        timeout: float,
        allow_attr_not_found: bool,
    ) -> bytes | None:
        end_at = _monotonic_seconds() + max(0.0, timeout)
        with self._condition:
            while True:
                for index, pdu in enumerate(self._response_queue):
                    result = self._handle_response_pdu(
                        pdu,
                        expected_opcodes=expected_opcodes,
                        request_opcode=request_opcode,
                        allow_attr_not_found=allow_attr_not_found,
                    )
                    if result is _PENDING:
                        continue
                    del self._response_queue[index]
                    return result
                remaining = end_at - _monotonic_seconds()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for ATT response")
                self._condition.wait(remaining)

    def _handle_response_pdu(
        self,
        pdu: bytes,
        *,
        expected_opcodes: set[int],
        request_opcode: int,
        allow_attr_not_found: bool,
    ):
        if not pdu:
            return _PENDING
        opcode = pdu[0]
        if opcode in expected_opcodes:
            return pdu
        if opcode == _ATT_OP_ERROR and len(pdu) >= 5 and pdu[1] == request_opcode:
            error_code = pdu[4]
            if allow_attr_not_found and error_code == _ATT_ERR_ATTR_NOT_FOUND:
                return None
            raise RuntimeError(
                f"ATT request 0x{request_opcode:02x} failed: handle=0x{struct.unpack_from('<H', pdu, 2)[0]:04x} error=0x{error_code:02x}"
            )
        self._dispatch_async_pdu(pdu)
        return _PENDING

    def _send_pdu(self, pdu: bytes) -> None:
        sock = self._require_socket()
        with self._send_lock:
            sock.sendall(pdu)

    def _recv_pdu(self, timeout: float) -> bytes:
        sock = self._require_socket()
        sock.settimeout(timeout)
        return sock.recv(max(self._mtu, _ATT_DEFAULT_MTU))

    def _start_rx_thread(self) -> None:
        if self._rx_thread is not None:
            return
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="timiniprint-linux-att", daemon=True)
        self._rx_thread.start()

    def _rx_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                pdu = self._recv_pdu(timeout=0.5)
            except TimeoutError:
                continue
            except OSError:
                break
            except Exception:
                break
            if not pdu:
                continue
            if self._dispatch_async_pdu(pdu):
                continue
            with self._condition:
                self._response_queue.append(pdu)
                self._condition.notify_all()

    def _dispatch_async_pdu(self, pdu: bytes) -> bool:
        if not pdu:
            return False
        opcode = pdu[0]
        if opcode not in {_ATT_OP_NOTIFY, _ATT_OP_INDICATE} or len(pdu) < 3:
            return False
        handle = struct.unpack_from("<H", pdu, 1)[0]
        payload = pdu[3:]
        callback = self._notify_callbacks.get(handle)
        if callback is not None:
            callback(handle, payload)
        if opcode == _ATT_OP_INDICATE:
            try:
                self._send_pdu(bytes([_ATT_OP_CONFIRM]))
            except Exception:
                pass
        return True

    def _require_socket(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("Not connected to ATT socket")
        return self._sock

    def _update_characteristic_mtu(self) -> None:
        max_payload = max(1, self._mtu - 3)
        for service in self.services:
            for characteristic in service.characteristics:
                characteristic.max_write_without_response_size = max_payload

    def _resolve_characteristic(self, char: Any) -> _LinuxAttCharacteristic | None:
        if isinstance(char, _LinuxAttCharacteristic):
            return char
        target_uuid = str(char).strip().lower()
        for service in self.services:
            for characteristic in service.characteristics:
                if characteristic.uuid == target_uuid:
                    return characteristic
        return None

    @staticmethod
    def _find_cccd(characteristic: _LinuxAttCharacteristic) -> _LinuxAttDescriptor | None:
        for descriptor in characteristic.descriptors:
            if descriptor.uuid == _uuid16(_UUID_CCCD):
                return descriptor
        return None


_PENDING = object()


def _monotonic_seconds() -> float:
    import time

    return time.monotonic()


def _open_att_socket(address: str, address_type: int, *, timeout: float) -> socket.socket:
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise RuntimeError("Python socket module lacks AF_BLUETOOTH support")
    sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_SEQPACKET, _BTPROTO_L2CAP)
    try:
        try:
            sock.setsockopt(_SOL_BLUETOOTH, _BT_SECURITY, struct.pack("I", _BT_SECURITY_LOW))
        except OSError:
            pass
        _bind_l2cap(sock, "00:00:00:00:00:00", _BDADDR_LE_PUBLIC)
        # The connect itself runs in blocking mode. Empirically, the
        # AF_BLUETOOTH/L2CAP kernel path treats non-blocking connect
        # (settimeout != None) differently and can hang indefinitely
        # without marking the socket writable, even for peers that
        # accept the connection cleanly in blocking mode. Apply the
        # caller-requested timeout only after the connect has succeeded
        # so it governs subsequent read/write operations.
        _connect_l2cap(sock, address, address_type, timeout=timeout)
        sock.settimeout(timeout)
        return sock
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


def _bind_l2cap(sock: socket.socket, address: str, address_type: int) -> None:
    sockaddr = _make_sockaddr_l2(address, address_type)
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.bind(sock.fileno(), ctypes.byref(sockaddr), ctypes.sizeof(sockaddr))
    if result != 0:
        errno = ctypes.get_errno()
        # Binding is useful for selecting the LE address type but some kernels
        # reject explicit ANY binds here. Connect may still succeed.
        if errno not in {22, 98}:
            raise OSError(errno, "L2CAP bind failed")


def _connect_l2cap(sock: socket.socket, address: str, address_type: int, *, timeout: float | None = None) -> None:
    sockaddr = _make_sockaddr_l2(address, address_type)
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.connect(sock.fileno(), ctypes.byref(sockaddr), ctypes.sizeof(sockaddr))
    if result == 0:
        return
    err = ctypes.get_errno()
    if err not in (errno_module.EINPROGRESS, errno_module.EALREADY, errno_module.EWOULDBLOCK):
        raise OSError(err, "L2CAP ATT connect failed")
    # When the caller put the socket in non-blocking mode (settimeout != None)
    # the raw libc.connect() returns EINPROGRESS even though the kernel is
    # still working on the L2CAP create-connection. Wait for the socket to
    # become writable, then read SO_ERROR.
    _, writable, _ = select.select([], [sock], [], timeout)
    if not writable:
        raise OSError(errno_module.ETIMEDOUT, "L2CAP ATT connect timed out")
    so_error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if so_error != 0:
        raise OSError(so_error, "L2CAP ATT connect failed")


def _make_sockaddr_l2(address: str, address_type: int) -> _SockaddrL2:
    sockaddr = _SockaddrL2()
    sockaddr.l2_family = _AF_BLUETOOTH
    sockaddr.l2_psm = 0
    raw = _bdaddr_bytes(address)
    for index, value in enumerate(raw):
        sockaddr.l2_bdaddr[index] = value
    sockaddr.l2_cid = _L2CAP_CID_ATT
    sockaddr.l2_bdaddr_type = address_type
    return sockaddr


def _bdaddr_bytes(address: str) -> bytes:
    compact = address.replace(":", "").strip()
    if len(compact) != 12:
        raise ValueError(f"Invalid Bluetooth address: {address}")
    return bytes.fromhex(compact)[::-1]


def _uuid16(value: int) -> str:
    return f"0000{value:04x}{_BASE_UUID_SUFFIX}"


def _decode_uuid(data: bytes) -> str:
    if len(data) == 2:
        return _uuid16(struct.unpack_from("<H", data, 0)[0])
    if len(data) == 16:
        return str(uuid.UUID(bytes_le=data)).lower()
    return data.hex()


def _properties_from_mask(mask: int) -> tuple[str, ...]:
    props: list[str] = []
    if mask & 0x02:
        props.append("read")
    if mask & 0x04:
        props.append("write-without-response")
    if mask & 0x08:
        props.append("write")
    if mask & 0x10:
        props.append("notify")
    if mask & 0x20:
        props.append("indicate")
    return tuple(props)
