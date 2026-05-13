"""Direct Linux LE GATT client over an L2CAP/ATT socket.

This bypasses bluetoothd's `Device1.Connect()` entirely, which on Linux/BlueZ
mis-routes BLE-only printers whose advertising packet sets the
"Simultaneous LE and BR/EDR (Controller)" flag down the BR/EDR Create
Connection path and times out with Page Timeout.

Implementation notes:

- Uses ``socket.AF_BLUETOOTH`` + ``BTPROTO_L2CAP`` with raw sockaddr_l2 via
  ``ctypes``. CPython's stdlib socket module does not (yet) expose the
  LE-public address-type tuple form on all builds.
- Provides a small ``LinuxLeL2capClient`` whose surface mirrors the subset
  of ``bleak.BleakClient`` that the project's ``_BleakTransportSession``
  consumes: ``services``, ``mtu_size``, ``write_gatt_char``, ``start_notify``,
  ``stop_notify``, ``disconnect``.
- Service / characteristic / descriptor discovery is performed against the
  remote ATT server using the standard primitives (Read By Group Type, Read
  By Type, Find Information). Once discovered, the client exposes objects
  that quack like Bleak's GATT objects: ``.uuid`` (lowercase string),
  ``.handle``, ``.properties`` (list of strings like ``"write-without-response"``,
  ``"notify"``), ``.characteristics``.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import os
import select
import socket
import struct
import threading
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from ..constants import IS_LINUX

# --- Bluetooth / L2CAP / ATT constants ---------------------------------------

_AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", None)
_BTPROTO_L2CAP = getattr(socket, "BTPROTO_L2CAP", None)
_BT_ATT_CID = 4                  # L2CAP fixed CID for ATT
_BT_ADDR_LE_PUBLIC = 1           # BDADDR_LE_PUBLIC
_BT_ADDR_LE_RANDOM = 2           # BDADDR_LE_RANDOM
_SOL_BLUETOOTH = 274
_BT_SECURITY = 4
_BT_SECURITY_LOW = 1

# ATT opcodes (subset)
ATT_OP_ERROR_RSP = 0x01
ATT_OP_EXCHANGE_MTU_REQ = 0x02
ATT_OP_EXCHANGE_MTU_RSP = 0x03
ATT_OP_FIND_INFO_REQ = 0x04
ATT_OP_FIND_INFO_RSP = 0x05
ATT_OP_READ_BY_TYPE_REQ = 0x08
ATT_OP_READ_BY_TYPE_RSP = 0x09
ATT_OP_READ_REQ = 0x0A
ATT_OP_READ_RSP = 0x0B
ATT_OP_READ_BY_GROUP_TYPE_REQ = 0x10
ATT_OP_READ_BY_GROUP_TYPE_RSP = 0x11
ATT_OP_WRITE_REQ = 0x12
ATT_OP_WRITE_RSP = 0x13
ATT_OP_HANDLE_VALUE_NOTIFY = 0x1B
ATT_OP_HANDLE_VALUE_INDICATE = 0x1D
ATT_OP_HANDLE_VALUE_CONFIRM = 0x1E
ATT_OP_WRITE_CMD = 0x52

# GATT attribute type UUIDs (16-bit form)
_UUID_PRIMARY_SERVICE = 0x2800
_UUID_CHARACTERISTIC_DECL = 0x2803
_UUID_CCCD = 0x2902              # Client Characteristic Configuration Descriptor

# ATT errors that mean "we've enumerated everything in this range"
_ATT_ERROR_ATTRIBUTE_NOT_FOUND = 0x0A

_DEFAULT_CLIENT_RX_MTU = 512

# How long to wait between "we wrote the last byte" and tearing down the
# L2CAP socket. Most LE thermal printers buffer the print job and render
# from that buffer; closing while the buffer still has data tears the
# link down and the printer aborts mid-row.
#
# We use a quiescence model rather than a fixed sleep: keep the socket
# open as long as the printer is talking to us (sending notifications),
# and close once it has been silent for `_DISCONNECT_IDLE_SECONDS`.
# `_DISCONNECT_MAX_GRACE_SECONDS` is a hard upper bound for the case
# where the firmware never emits a final notification.
#
# Both can be overridden per-host via env vars:
#   TIMINI_BLE_DISCONNECT_IDLE_S    (default 1.5)
#   TIMINI_BLE_DISCONNECT_MAX_S     (default 30)
_DISCONNECT_IDLE_SECONDS = 1.5
_DISCONNECT_MAX_GRACE_SECONDS = 30.0

_PROP_BITS = {
    0x02: "read",
    0x04: "write-without-response",
    0x08: "write",
    0x10: "notify",
    0x20: "indicate",
}


def af_bluetooth_available() -> bool:
    """Return True iff this Python build exposes the AF_BLUETOOTH/L2CAP stack."""
    return IS_LINUX and _AF_BLUETOOTH is not None and _BTPROTO_L2CAP is not None


# --- Address packing ---------------------------------------------------------


def _bdaddr_bytes_le(addr: str) -> bytes:
    parts = [int(p, 16) for p in addr.split(":")]
    if len(parts) != 6:
        raise ValueError(f"Invalid BD address: {addr!r}")
    return bytes(reversed(parts))


def _pack_sockaddr_l2(bdaddr: str, psm: int, cid: int, addr_type: int) -> bytes:
    """Pack a Linux ``struct sockaddr_l2`` (14 bytes including alignment pad)."""
    return (
        struct.pack("<HH", _AF_BLUETOOTH or 31, psm)
        + _bdaddr_bytes_le(bdaddr)
        + struct.pack("<HB", cid, addr_type)
        + b"\x00"
    )


# --- UUID helpers ------------------------------------------------------------


def _uuid_to_128bit_lower(uuid_str: str) -> str:
    """Normalize any 16/32/128-bit UUID string to 128-bit lowercase."""
    s = uuid_str.strip().lower()
    if len(s) == 4:  # 16-bit form
        return f"0000{s}-0000-1000-8000-00805f9b34fb"
    if len(s) == 8:  # 32-bit form
        return f"{s}-0000-1000-8000-00805f9b34fb"
    return s


def _uuid_short_from_bytes_le(buf: bytes) -> str:
    """Convert an ATT-encoded little-endian UUID payload to a lowercase 128-bit string."""
    if len(buf) == 2:
        value = struct.unpack_from("<H", buf, 0)[0]
        return _uuid_to_128bit_lower(f"{value:04x}")
    if len(buf) == 16:
        return str(_uuid_from_le_bytes(buf))
    raise ValueError(f"Unexpected ATT UUID length {len(buf)}")


def _uuid_from_le_bytes(buf: bytes) -> str:
    # Bluetooth represents 128-bit UUIDs little-endian on the wire;
    # convert to standard 8-4-4-4-12 form.
    b = buf[::-1]
    return (
        f"{b[0:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-{b[8:10].hex()}-{b[10:16].hex()}"
    )


def _properties_from_bits(bits: int) -> List[str]:
    return [name for value, name in _PROP_BITS.items() if bits & value]


# --- GATT object surface (Bleak-compatible duck types) -----------------------


class _LinuxLeDescriptor:
    __slots__ = ("uuid", "handle", "_value_handle")

    def __init__(self, uuid: str, handle: int, value_handle: int) -> None:
        self.uuid = uuid
        self.handle = handle
        self._value_handle = value_handle  # for parent characteristic linkage

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<descriptor uuid={self.uuid} handle=0x{self.handle:04x}>"


class _LinuxLeCharacteristic:
    __slots__ = (
        "uuid",
        "handle",            # declaration handle
        "value_handle",
        "properties",
        "_prop_bits",
        "descriptors",
        "service",
    )

    def __init__(
        self,
        uuid: str,
        decl_handle: int,
        value_handle: int,
        prop_bits: int,
    ) -> None:
        self.uuid = uuid
        # Bleak's `BleakGATTCharacteristic.handle` is the *value* handle.
        # `_BleakTransportSession` uses `getattr(char, "uuid", "")` and
        # the value handle implicitly via Bleak's own GATT writes. Expose
        # both for clarity and keep ``handle`` as the value handle to match
        # Bleak's behavior.
        self.handle = value_handle
        self.value_handle = value_handle
        self._prop_bits = prop_bits
        self.properties = _properties_from_bits(prop_bits)
        self.descriptors: List[_LinuxLeDescriptor] = []
        self.service: Optional["_LinuxLeService"] = None

    @property
    def cccd_handle(self) -> Optional[int]:
        for desc in self.descriptors:
            if desc.uuid == _uuid_to_128bit_lower(f"{_UUID_CCCD:04x}"):
                return desc.handle
        return None

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<char uuid={self.uuid} handle=0x{self.handle:04x} props={self.properties}>"
        )


class _LinuxLeService:
    __slots__ = ("uuid", "handle", "end_handle", "characteristics")

    def __init__(self, uuid: str, handle: int, end_handle: int) -> None:
        self.uuid = uuid
        self.handle = handle
        self.end_handle = end_handle
        self.characteristics: List[_LinuxLeCharacteristic] = []

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<service uuid={self.uuid} range=0x{self.handle:04x}..0x{self.end_handle:04x}"
            f" chars={len(self.characteristics)}>"
        )


class _LinuxLeServiceCollection:
    """Iterable like Bleak's ``BleakGATTServiceCollection`` (just enough surface)."""

    def __init__(self, services: List[_LinuxLeService]) -> None:
        self._services = services

    def __iter__(self):
        return iter(self._services)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._services)

    def get_characteristic(self, uuid: str) -> Optional[_LinuxLeCharacteristic]:
        target = _uuid_to_128bit_lower(uuid)
        for svc in self._services:
            for char in svc.characteristics:
                if char.uuid == target:
                    return char
        return None


# --- Low-level L2CAP/ATT plumbing -------------------------------------------


def _resolve_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


class _L2capAttLink:
    """Blocking L2CAP/ATT socket with a synchronous request/response API and
    a background reader thread dispatching Handle Value Notifications to a
    callback."""

    def __init__(self) -> None:
        if not af_bluetooth_available():
            raise RuntimeError(
                "Linux LE L2CAP transport requires socket.AF_BLUETOOTH. "
                "Use a Python built with bluez development headers "
                "(distro Python, or pyenv with libbluetooth-dev installed)."
            )
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pending_responses: Dict[int, threading.Event] = {}
        self._pending_response_buf: Dict[int, bytes] = {}
        self._notify_handlers: Dict[int, Callable[[int, bytes], None]] = {}
        self._connected = False
        # Updated by the reader thread every time the peer sends us bytes.
        # disconnect() uses this to wait for quiescence before closing so the
        # printer can finish rendering a job buffered on its side.
        self._last_peer_activity_monotonic = 0.0
        self._libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self._libc.bind.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        self._libc.bind.restype = ctypes.c_int
        self._libc.connect.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        self._libc.connect.restype = ctypes.c_int

    # ----- lifecycle -----

    def open(self, bdaddr: str, addr_type: int = _BT_ADDR_LE_PUBLIC) -> None:
        sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_SEQPACKET, _BTPROTO_L2CAP)
        try:
            local = _pack_sockaddr_l2("00:00:00:00:00:00", 0, _BT_ATT_CID, addr_type)
            if self._libc.bind(sock.fileno(), local, len(local)) != 0:
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err), "bind sockaddr_l2")
            sock.setsockopt(
                _SOL_BLUETOOTH, _BT_SECURITY, struct.pack("BB", _BT_SECURITY_LOW, 0)
            )
            remote = _pack_sockaddr_l2(bdaddr, 0, _BT_ATT_CID, addr_type)
            if self._libc.connect(sock.fileno(), remote, len(remote)) != 0:
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err), "connect sockaddr_l2")
        except Exception:
            sock.close()
            raise
        self._sock = sock
        self._connected = True
        self._last_peer_activity_monotonic = time.monotonic()
        self._stop.clear()
        self._reader = threading.Thread(
            target=self._reader_loop, name="l2cap-att-reader", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        self._connected = False
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)

    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    def wait_for_quiescence(self, idle_seconds: float, max_seconds: float) -> None:
        """Block until the peer has been silent for `idle_seconds`, capped
        by `max_seconds` total. Used by disconnect() so the printer can
        finish rendering a buffered job before we close the link."""
        if max_seconds <= 0:
            return
        deadline = time.monotonic() + max_seconds
        while self._connected:
            now = time.monotonic()
            if now >= deadline:
                return
            quiet_for = now - self._last_peer_activity_monotonic
            remaining_idle = idle_seconds - quiet_for
            if remaining_idle <= 0:
                return
            time.sleep(min(remaining_idle, deadline - now, 0.2))

    # ----- notifications -----

    def set_notify_handler(
        self, value_handle: int, handler: Optional[Callable[[int, bytes], None]]
    ) -> None:
        if handler is None:
            self._notify_handlers.pop(value_handle, None)
        else:
            self._notify_handlers[value_handle] = handler

    # ----- raw send/recv -----

    def _send_bytes(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise RuntimeError("L2CAP socket not open")
        sock.send(data)

    def write_command(self, value_handle: int, value: bytes) -> None:
        self._send_bytes(struct.pack("<BH", ATT_OP_WRITE_CMD, value_handle) + value)

    def request(self, pdu: bytes, expect_op: int, timeout: float = 5.0) -> bytes:
        """Send a request, block until the matching response opcode arrives."""
        evt = threading.Event()
        with self._lock:
            self._pending_responses[expect_op] = evt
            self._pending_response_buf.pop(expect_op, None)
            self._send_bytes(pdu)
        if not evt.wait(timeout=timeout):
            with self._lock:
                self._pending_responses.pop(expect_op, None)
            raise TimeoutError(f"ATT request timed out (op 0x{pdu[0]:02x})")
        with self._lock:
            data = self._pending_response_buf.pop(expect_op, b"")
            self._pending_responses.pop(expect_op, None)
        return data

    # ----- background reader -----

    def _reader_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        # Use select() to poll for readability instead of socket.settimeout().
        # The latter affects sock.send() too, which would silently raise an
        # empty TimeoutError if the kernel buffer pauses for >timeout seconds.
        fd = sock.fileno()
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                data = sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            self._last_peer_activity_monotonic = time.monotonic()
            self._dispatch(data)

    def _dispatch(self, data: bytes) -> None:
        op = data[0]
        if op == ATT_OP_HANDLE_VALUE_NOTIFY and len(data) >= 3:
            handle = struct.unpack_from("<H", data, 1)[0]
            value = data[3:]
            handler = self._notify_handlers.get(handle)
            if handler is not None:
                try:
                    handler(handle, value)
                except Exception:
                    pass
            return
        if op == ATT_OP_HANDLE_VALUE_INDICATE and len(data) >= 3:
            # Acknowledge indications so the server stays unblocked.
            try:
                self._send_bytes(bytes([ATT_OP_HANDLE_VALUE_CONFIRM]))
            except OSError:
                pass
            return
        # Treat any error response as fulfilling its underlying request type.
        if op == ATT_OP_ERROR_RSP and len(data) >= 5:
            req_op = data[1]
            expected_rsp = req_op + 1  # ATT response opcodes are req+1 by convention
            self._complete_pending(expected_rsp, data) or self._complete_pending(op, data)
            return
        if not self._complete_pending(op, data):
            # Unsolicited / mid-stream — ignore for now.
            return

    def _complete_pending(self, op: int, data: bytes) -> bool:
        with self._lock:
            evt = self._pending_responses.get(op)
            if evt is None:
                return False
            self._pending_response_buf[op] = data
        evt.set()
        return True


# --- High-level Bleak-compatible client --------------------------------------


class LinuxLeL2capClient:
    """Drop-in replacement for the subset of ``bleak.BleakClient`` that
    ``_BleakTransportSession`` uses, backed by a direct LE L2CAP/ATT socket.

    Methods that Bleak exposes as ``async`` are also ``async`` here so the
    transport session can ``await`` them uniformly. The underlying socket
    work is synchronous; we schedule it on the running loop's executor when
    needed.
    """

    def __init__(self, address: str, *, addr_type: int = _BT_ADDR_LE_PUBLIC) -> None:
        self._address = address
        self._addr_type = addr_type
        self._link = _L2capAttLink()
        self._services_obj: Optional[_LinuxLeServiceCollection] = None
        self._raw_services: List[_LinuxLeService] = []
        self._mtu_size: int = 23
        self._notify_callbacks_by_value_handle: Dict[int, Callable[[Any, bytes], None]] = {}
        # Captured at start_notify time so we can hop notification callbacks
        # from the reader thread onto the asyncio loop thread. The V5X runtime
        # (and Bleak's transport plumbing) calls asyncio.Event.set() inside
        # the notification handler, which is not thread-safe across loops.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ----- Bleak API surface -----

    @property
    def address(self) -> str:
        return self._address

    @property
    def is_connected(self) -> bool:
        return self._link.is_connected()

    @property
    def mtu_size(self) -> int:
        return self._mtu_size

    @property
    def services(self) -> _LinuxLeServiceCollection:
        if self._services_obj is None:
            self._services_obj = _LinuxLeServiceCollection(self._raw_services)
        return self._services_obj

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_connect)

    async def disconnect(self) -> None:
        # Wait for the printer to finish rendering whatever it has buffered
        # before tearing down the link. Closing while the printer is still
        # rendering aborts the print mid-row on V5X-family firmwares.
        # Heuristic: keep the link open as long as notifications keep
        # arriving (the printer is doing work). Close once it has been
        # silent for `idle_seconds`, with a hard cap at `max_seconds`.
        idle = _resolve_env_float("TIMINI_BLE_DISCONNECT_IDLE_S", _DISCONNECT_IDLE_SECONDS)
        cap = _resolve_env_float("TIMINI_BLE_DISCONNECT_MAX_S", _DISCONNECT_MAX_GRACE_SECONDS)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._link.wait_for_quiescence, idle, cap)
        except Exception:
            pass
        await loop.run_in_executor(None, self._link.close)

    async def write_gatt_char(self, char, data: bytes, response: bool = False) -> None:
        value_handle = _resolve_value_handle(char, self._raw_services)
        if value_handle is None:
            raise RuntimeError(f"Unknown characteristic: {char!r}")
        loop = asyncio.get_event_loop()
        if response:
            await loop.run_in_executor(
                None,
                self._sync_write_request,
                value_handle,
                bytes(data),
            )
        else:
            await loop.run_in_executor(
                None,
                self._link.write_command,
                value_handle,
                bytes(data),
            )

    async def start_notify(
        self, uuid: str, callback: Callable[[Any, bytes], None]
    ) -> None:
        char = self.services.get_characteristic(uuid)
        if char is None or char.cccd_handle is None:
            raise RuntimeError(f"No CCCD descriptor for {uuid}")
        value_handle = char.value_handle
        loop = asyncio.get_event_loop()
        self._loop = loop

        # The reader thread calls `_dispatch` directly, but Bleak's transport
        # plumbing and the V5X runtime expect notification callbacks to run on
        # the asyncio loop thread (they call `asyncio.Event.set()`, which is
        # only safe from inside the loop). Hop via `call_soon_threadsafe`.
        def _dispatch(_handle: int, value: bytes) -> None:
            target_loop = self._loop
            if target_loop is None or target_loop.is_closed():
                return
            try:
                target_loop.call_soon_threadsafe(callback, char, value)
            except RuntimeError:
                # Loop already closed; drop silently.
                return

        self._link.set_notify_handler(value_handle, _dispatch)
        self._notify_callbacks_by_value_handle[value_handle] = callback
        await loop.run_in_executor(
            None, self._sync_write_request, char.cccd_handle, b"\x01\x00"
        )

    async def stop_notify(self, uuid: str) -> None:
        char = self.services.get_characteristic(uuid)
        if char is None or char.cccd_handle is None:
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._sync_write_request, char.cccd_handle, b"\x00\x00"
            )
        except Exception:
            pass
        self._link.set_notify_handler(char.value_handle, None)
        self._notify_callbacks_by_value_handle.pop(char.value_handle, None)

    # ----- internals -----

    def _sync_connect(self) -> None:
        self._link.open(self._address, self._addr_type)
        self._mtu_size = self._sync_exchange_mtu(_DEFAULT_CLIENT_RX_MTU)
        self._raw_services = self._sync_discover_services()
        for svc in self._raw_services:
            chars = self._sync_discover_characteristics(svc)
            svc.characteristics = chars
            for char in chars:
                char.service = svc
                char.descriptors = self._sync_discover_descriptors(svc, char)
        self._services_obj = _LinuxLeServiceCollection(self._raw_services)

    def _sync_exchange_mtu(self, client_rx_mtu: int) -> int:
        pdu = struct.pack("<BH", ATT_OP_EXCHANGE_MTU_REQ, client_rx_mtu)
        try:
            rsp = self._link.request(pdu, ATT_OP_EXCHANGE_MTU_RSP, timeout=3.0)
        except TimeoutError:
            return 23
        if not rsp or rsp[0] != ATT_OP_EXCHANGE_MTU_RSP or len(rsp) < 3:
            return 23
        server_mtu = struct.unpack_from("<H", rsp, 1)[0]
        return min(client_rx_mtu, server_mtu)

    def _sync_write_request(self, value_handle: int, value: bytes) -> None:
        pdu = struct.pack("<BH", ATT_OP_WRITE_REQ, value_handle) + value
        rsp = self._link.request(pdu, ATT_OP_WRITE_RSP, timeout=3.0)
        if not rsp or rsp[0] != ATT_OP_WRITE_RSP:
            if rsp and rsp[0] == ATT_OP_ERROR_RSP:
                raise RuntimeError(f"ATT Write Request error: {rsp.hex()}")
            raise RuntimeError(
                f"Unexpected response to Write Request: {rsp.hex() if rsp else '(empty)'}"
            )

    def _sync_discover_services(self) -> List[_LinuxLeService]:
        services: List[_LinuxLeService] = []
        start_handle = 0x0001
        while start_handle <= 0xFFFF:
            pdu = struct.pack(
                "<BHHH",
                ATT_OP_READ_BY_GROUP_TYPE_REQ,
                start_handle,
                0xFFFF,
                _UUID_PRIMARY_SERVICE,
            )
            try:
                rsp = self._link.request(pdu, ATT_OP_READ_BY_GROUP_TYPE_RSP, timeout=3.0)
            except TimeoutError:
                break
            if not rsp:
                break
            if rsp[0] == ATT_OP_ERROR_RSP:
                # End-of-range: AttributeNotFound stops the discovery.
                break
            if rsp[0] != ATT_OP_READ_BY_GROUP_TYPE_RSP or len(rsp) < 2:
                break
            entry_len = rsp[1]
            payload = rsp[2:]
            if entry_len < 6 or len(payload) % entry_len != 0:
                break
            last_end = start_handle - 1
            for i in range(0, len(payload), entry_len):
                entry = payload[i : i + entry_len]
                handle = struct.unpack_from("<H", entry, 0)[0]
                end_handle = struct.unpack_from("<H", entry, 2)[0]
                uuid_bytes = entry[4:]
                uuid = _uuid_short_from_bytes_le(uuid_bytes)
                services.append(_LinuxLeService(uuid, handle, end_handle))
                last_end = max(last_end, end_handle)
            if last_end >= 0xFFFF:
                break
            start_handle = last_end + 1
        return services

    def _sync_discover_characteristics(
        self, svc: _LinuxLeService
    ) -> List[_LinuxLeCharacteristic]:
        chars: List[_LinuxLeCharacteristic] = []
        start_handle = svc.handle
        while start_handle <= svc.end_handle:
            pdu = struct.pack(
                "<BHHH",
                ATT_OP_READ_BY_TYPE_REQ,
                start_handle,
                svc.end_handle,
                _UUID_CHARACTERISTIC_DECL,
            )
            try:
                rsp = self._link.request(pdu, ATT_OP_READ_BY_TYPE_RSP, timeout=3.0)
            except TimeoutError:
                break
            if not rsp or rsp[0] == ATT_OP_ERROR_RSP:
                break
            if rsp[0] != ATT_OP_READ_BY_TYPE_RSP or len(rsp) < 2:
                break
            entry_len = rsp[1]
            payload = rsp[2:]
            if entry_len < 7 or len(payload) % entry_len != 0:
                break
            last_handle = start_handle - 1
            for i in range(0, len(payload), entry_len):
                entry = payload[i : i + entry_len]
                decl_handle = struct.unpack_from("<H", entry, 0)[0]
                # Characteristic Declaration value layout:
                #   1 byte properties | 2 bytes value handle (LE) | 2/16 bytes UUID
                props = entry[2]
                value_handle = struct.unpack_from("<H", entry, 3)[0]
                uuid_bytes = entry[5:]
                uuid = _uuid_short_from_bytes_le(uuid_bytes)
                chars.append(
                    _LinuxLeCharacteristic(uuid, decl_handle, value_handle, props)
                )
                last_handle = max(last_handle, decl_handle)
            if last_handle >= svc.end_handle:
                break
            start_handle = last_handle + 1
        return chars

    def _sync_discover_descriptors(
        self, svc: _LinuxLeService, char: _LinuxLeCharacteristic
    ) -> List[_LinuxLeDescriptor]:
        # Descriptors live in [value_handle + 1 .. next_char_decl - 1] (or end of svc).
        idx = svc.characteristics.index(char) if char in svc.characteristics else None
        if idx is not None and idx + 1 < len(svc.characteristics):
            end = svc.characteristics[idx + 1].handle - 1
        else:
            end = svc.end_handle
        start = char.value_handle + 1
        if start > end:
            return []
        descriptors: List[_LinuxLeDescriptor] = []
        while start <= end:
            pdu = struct.pack("<BHH", ATT_OP_FIND_INFO_REQ, start, end)
            try:
                rsp = self._link.request(pdu, ATT_OP_FIND_INFO_RSP, timeout=2.0)
            except TimeoutError:
                break
            if not rsp or rsp[0] == ATT_OP_ERROR_RSP:
                break
            if rsp[0] != ATT_OP_FIND_INFO_RSP or len(rsp) < 2:
                break
            fmt = rsp[1]                              # 0x01 = 16-bit UUIDs, 0x02 = 128-bit
            entry_len = 4 if fmt == 0x01 else 18
            payload = rsp[2:]
            if entry_len <= 0 or len(payload) % entry_len != 0:
                break
            last_handle = start - 1
            for i in range(0, len(payload), entry_len):
                entry = payload[i : i + entry_len]
                handle = struct.unpack_from("<H", entry, 0)[0]
                uuid_bytes = entry[2:]
                uuid = _uuid_short_from_bytes_le(uuid_bytes)
                descriptors.append(_LinuxLeDescriptor(uuid, handle, char.value_handle))
                last_handle = max(last_handle, handle)
            if last_handle >= end:
                break
            start = last_handle + 1
        return descriptors


def _resolve_value_handle(char, services: Iterable[_LinuxLeService]) -> Optional[int]:
    """Resolve a write target either from a characteristic object or a UUID string."""
    if isinstance(char, _LinuxLeCharacteristic):
        return char.value_handle
    handle = getattr(char, "handle", None)
    if isinstance(handle, int):
        return handle
    uuid = getattr(char, "uuid", None) if not isinstance(char, str) else char
    if uuid is None:
        return None
    target = _uuid_to_128bit_lower(str(uuid))
    for svc in services:
        for c in svc.characteristics:
            if c.uuid == target:
                return c.value_handle
    return None
