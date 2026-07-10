from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from ...protocol.families.v5c import (
    V5C_CONNECT_INIT_PACKET,
    V5C_NOTIFY_PAUSE,
    V5C_NOTIFY_RESUME,
    V5C_QUERY_STATUS_PACKET,
)
from ...protocol.steps import ProtocolStepOperation
from .base import RuntimeController


@dataclass
class _V5CCompatibilityState:
    mode: str = "unknown"
    request_pending: bool = False
    checked: bool = False
    confirmed: Optional[bool] = None
    last_result_code: Optional[int] = None
    backend_write_cmd: bytes = b""
    last_trigger_opcode: Optional[int] = None
    last_trigger_packet: bytes = b""


@dataclass
class _V5CSessionState:
    status_code: Optional[int] = None
    status_name: str = "unknown"
    is_charging: bool = False
    query_status_in_flight: bool = False
    print_complete_seen: bool = False
    max_print_height: Optional[int] = None
    device_serial: str = ""
    serial_valid: Optional[bool] = None
    last_auth_payload: bytes = b""
    last_error_status: Optional[int] = None
    compatibility: _V5CCompatibilityState = field(default_factory=_V5CCompatibilityState)


class V5CRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._state = _V5CSessionState()

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, V5CRuntimeController):
            self._state = previous._state

    def debug_snapshot(self) -> dict[str, object]:
        compatibility = self._state.compatibility
        return {
            "status_code": self._state.status_code,
            "status_name": self._state.status_name,
            "is_charging": self._state.is_charging,
            "query_status_in_flight": self._state.query_status_in_flight,
            "print_complete_seen": self._state.print_complete_seen,
            "max_print_height": self._state.max_print_height,
            "device_serial": self._state.device_serial,
            "serial_valid": self._state.serial_valid,
            "last_auth_payload": self._state.last_auth_payload,
            "last_error_status": self._state.last_error_status,
            "compatibility": {
                "mode": compatibility.mode,
                "request_pending": compatibility.request_pending,
                "checked": compatibility.checked,
                "confirmed": compatibility.confirmed,
                "last_result_code": compatibility.last_result_code,
                "backend_write_cmd": compatibility.backend_write_cmd,
                "last_trigger_opcode": compatibility.last_trigger_opcode,
                "last_trigger_packet": compatibility.last_trigger_packet,
            },
        }

    def debug_update(self, **changes: object) -> None:
        for key, value in changes.items():
            if not hasattr(self._state, key):
                raise KeyError(f"Unknown V5C debug field '{key}'")
            setattr(self._state, key, value)

    async def initialize_connection(self, session, *, mtu_size: int, timeout: float) -> None:
        _ = mtu_size
        await asyncio.sleep(0.6)
        sent = await session.send_control_packet(V5C_CONNECT_INIT_PACKET, timeout=timeout)
        if not sent:
            raise RuntimeError("V5C connect init send unavailable")

    def handle_notification(self, session, payload: bytes) -> None:
        if payload == V5C_NOTIFY_PAUSE:
            session.set_flow_paused(True, payload=payload)
            return
        if payload == V5C_NOTIFY_RESUME:
            session.set_flow_paused(False, payload=payload)
            return
        opcode = session.extract_prefixed_opcode(payload)
        if opcode == 0xA1:
            self._update_status(session, payload)
        elif opcode == 0xAA:
            self._update_max_print_height(session, payload)
        elif opcode in (0xA8, 0xA9):
            self._update_compatibility(session, payload, opcode)

    async def send_protocol_steps(self, session, steps, *, timeout: float) -> bool:
        _ = timeout
        if not session.can_send_standard_payload():
            return False
        if any(step.operation is not ProtocolStepOperation.SEND for step in steps):
            return False
        # Named V5C steps remain one standard stream; their logical boundary is
        # not a BLE write boundary.
        data = b"".join(step.data for step in steps)
        query_status = any(step.data == V5C_QUERY_STATUS_PACKET for step in steps)
        self._state.query_status_in_flight = query_status
        try:
            await session.send_standard_payload(data)
        except Exception:
            if query_status:
                self._state.query_status_in_flight = False
            raise
        return True

    def build_compat_request(
        self,
        *,
        ble_name: str,
        ble_address: str,
        ble_model: str = "V5C",
    ) -> Optional[dict[str, str]]:
        compat = self._state.compatibility
        if not compat.request_pending:
            return None
        mode = compat.mode
        if mode == "to_auth":
            packet = compat.last_trigger_packet
            if not packet:
                return None
            return {
                "mode": mode,
                "ble_name": ble_name,
                "ble_address": ble_address,
                "ble_sn": packet.hex(),
                "ble_model": ble_model,
            }
        if mode in {"get_sn", "auth"}:
            serial = self._state.device_serial or "0"
            return {
                "mode": mode,
                "ble_name": ble_name,
                "ble_address": ble_address,
                "ble_sn": serial,
                "ble_model": ble_model,
            }
        return None

    def apply_compat_result(self, session, *, mode: str, result_code: Optional[int], write_cmd: bytes | None = None) -> None:
        compat = self._state.compatibility
        compat.mode = mode
        compat.request_pending = False
        compat.checked = True
        compat.last_result_code = result_code
        compat.backend_write_cmd = write_cmd or b""
        compat.confirmed = None if result_code is None else result_code != -2
        if result_code == -2:
            session.report_warning(
                short="V5C compatibility check failed",
                detail=f"mode={mode}. Continuing without blocking the print session.",
            )

    def _update_status(self, session, payload: bytes) -> None:
        raw = session.extract_prefixed_payload(payload)
        if not raw:
            return
        previous_status = self._state.status_code
        status = raw[0]
        self._state.status_code = status
        self._state.status_name = self._status_name(status)
        self._state.is_charging = status in (0x10, 0x11)
        if status == 0x80:
            self._state.print_complete_seen = False
        elif status == 0x00:
            if self._state.query_status_in_flight:
                self._state.query_status_in_flight = False
            elif previous_status == 0x80:
                self._state.print_complete_seen = True
        self._handle_status(session, status)

    @staticmethod
    def _status_name(status: int) -> str:
        if status == 0x00:
            return "normal"
        if status == 0x80:
            return "printing"
        if status in (0x10, 0x11):
            return "charging"
        if status in (0x01, 0x02, 0x03):
            return "attention"
        if status == 0x04:
            return "overheat"
        if status == 0x08:
            return "low_power"
        return f"0x{status:02x}"

    def _handle_status(self, session, status: int) -> None:
        if status in (0x00, 0x80, 0x10, 0x11):
            self._state.last_error_status = None
            return
        if self._state.last_error_status == status:
            return
        self._state.last_error_status = status
        if status in (0x01, 0x02, 0x03):
            short = "V5C printer reported an attention state"
        elif status == 0x04:
            short = "V5C printer reported an overheat state"
        elif status == 0x08:
            short = "V5C printer reported a low-power state"
        else:
            short = "V5C printer reported an error status"
        session.report_warning(short=short, detail=f"status=0x{status:02x} ({self._state.status_name}).")

    def _update_max_print_height(self, session, payload: bytes) -> None:
        raw = session.extract_prefixed_payload(payload)
        if raw is None or len(raw) < 2:
            return
        self._state.max_print_height = int.from_bytes(raw[:2], "little")

    def _update_compatibility(self, session, payload: bytes, opcode: int) -> None:
        raw = session.extract_prefixed_payload(payload)
        if raw is None:
            return
        self._state.last_auth_payload = raw
        compat = self._state.compatibility
        compat.last_trigger_opcode = opcode
        compat.last_trigger_packet = payload
        if opcode == 0xA8:
            self._state.device_serial = ""
            self._state.serial_valid = None
            self._set_compatibility_mode("to_auth")
            compat.request_pending = True
            return
        serial_hex = raw[:8].hex()
        self._state.device_serial = serial_hex
        self._state.serial_valid = bool(serial_hex) and int(serial_hex, 16) != 0
        self._refresh_compatibility_mode()
        compat.request_pending = True

    def _refresh_compatibility_mode(self) -> None:
        if self._state.serial_valid is False:
            mode = "get_sn"
        elif self._state.serial_valid is True:
            mode = "auth"
        else:
            mode = "unknown"
        self._set_compatibility_mode(mode)

    def _set_compatibility_mode(self, mode: str) -> None:
        compat = self._state.compatibility
        compat.mode = mode
        compat.request_pending = False
        compat.checked = False
        compat.confirmed = None
        compat.last_result_code = None
        compat.backend_write_cmd = b""
