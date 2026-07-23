from __future__ import annotations

from collections.abc import Callable

from ... import reporting


class RuntimeConnectionSession:
    """Runtime-controller session adapter built on top of a live connection."""

    def __init__(self, connection, *, reporter: reporting.Reporter) -> None:
        self._connection = connection
        self._reporter = reporter

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float) -> None:
        attach_runtime_controller = getattr(self._connection, "attach_runtime_controller", None)
        if not callable(attach_runtime_controller):
            return
        await attach_runtime_controller(runtime_controller, timeout=timeout)

    def report_debug(self, message: str) -> None:
        self._reporter.debug(short="Runtime", detail=message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self._reporter.warning(short=short, detail=detail)

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        set_flow_paused = getattr(self._connection, "set_flow_paused", None)
        if callable(set_flow_paused):
            set_flow_paused(paused, payload=payload)

    def can_send_control_packet(self) -> bool:
        can_send_control_packet = getattr(self._connection, "can_send_control_packet", None)
        if callable(can_send_control_packet):
            return bool(can_send_control_packet())
        send_control_packet = getattr(self._connection, "send_control_packet", None)
        return callable(send_control_packet)

    def can_query_control_packet(self) -> bool:
        can_query_control_packet = getattr(self._connection, "can_query_control_packet", None)
        if callable(can_query_control_packet):
            return bool(can_query_control_packet())
        query_control_packet = getattr(self._connection, "query_control_packet", None)
        return callable(query_control_packet)

    def can_wait_for_notification(self) -> bool:
        can_wait_for_notification = getattr(self._connection, "can_wait_for_notification", None)
        if callable(can_wait_for_notification):
            return bool(can_wait_for_notification())
        return False

    def can_wait_for_reply(self) -> bool:
        can_wait_for_reply = getattr(self._connection, "can_wait_for_reply", None)
        if callable(can_wait_for_reply):
            return bool(can_wait_for_reply())
        return False

    def can_send_control_packet_wait_notification(self) -> bool:
        can_send_wait = getattr(self._connection, "can_send_control_packet_wait_notification", None)
        if callable(can_send_wait):
            return bool(can_send_wait())
        return False

    def can_send_standard_payload(self) -> bool:
        send_standard_payload = getattr(self._connection, "send_standard_payload", None)
        return callable(send_standard_payload)

    def can_send_bulk_payload(self) -> bool:
        can_send_bulk_payload = getattr(self._connection, "can_send_bulk_payload", None)
        if callable(can_send_bulk_payload):
            return bool(can_send_bulk_payload())
        return False

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        send_control_packet = getattr(self._connection, "send_control_packet", None)
        if not callable(send_control_packet):
            return False
        return await send_control_packet(packet, timeout=timeout)

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        query_control_packet = getattr(self._connection, "query_control_packet", None)
        if not callable(query_control_packet):
            return None
        if reply_complete is None:
            return await query_control_packet(packet, timeout=timeout)
        return await query_control_packet(
            packet,
            timeout=timeout,
            reply_complete=reply_complete,
        )

    async def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        wait_for_notification = getattr(self._connection, "wait_for_notification", None)
        if not callable(wait_for_notification):
            if required:
                raise RuntimeError("Connection does not support BLE notification waits")
            return None
        return await wait_for_notification(
            label,
            match,
            timeout=timeout,
            required=required,
        )

    async def wait_for_reply(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        wait_for_reply = getattr(self._connection, "wait_for_reply", None)
        if not callable(wait_for_reply):
            if required:
                raise RuntimeError("Connection does not support passive protocol reply waits")
            return None
        return await wait_for_reply(
            label,
            match,
            timeout=timeout,
            required=required,
        )

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match: Callable[[bytes], bool],
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        send_wait = getattr(self._connection, "send_control_packet_wait_notification", None)
        if not callable(send_wait) or not self.can_send_control_packet_wait_notification():
            if required:
                raise RuntimeError("Connection does not support atomic BLE notification queries")
            return None
        return await send_wait(
            packet,
            label=label,
            match=match,
            timeout=timeout,
            required=required,
        )

    async def send_standard_payload(self, data: bytes) -> None:
        send_standard_payload = getattr(self._connection, "send_standard_payload", None)
        if not callable(send_standard_payload):
            raise RuntimeError("Connection does not support runtime standard payload sends")
        await send_standard_payload(data)

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool:
        send_bulk_payload = getattr(self._connection, "send_bulk_payload", None)
        if not callable(send_bulk_payload):
            return False
        return await send_bulk_payload(data, timeout=timeout)
