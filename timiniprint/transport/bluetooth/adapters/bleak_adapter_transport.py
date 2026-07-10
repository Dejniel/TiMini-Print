"""Family-agnostic BLE transport helpers for the bleak adapter."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from .... import reporting
from ....devices.bluetooth_profiles import BleBulkWriteProfile, BleTransportProfile
from .bleak_adapter_diagnostics import (
    BleWriteCounters,
    BleWriteProgress,
    report_ble_disconnect_state,
    report_ble_split_bulk_plan,
    report_ble_write_plan,
    report_ble_write_summary,
)
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver, _WriteSelection


_NOTIFICATION_HISTORY_LIMIT = 16
_NOTIFICATION_HISTORY_TTL_SEC = 5.0


@dataclass
class _BleakBindings:
    write_char: Any = None
    bulk_write_char: Any = None
    notify_char: Any = None
    write_selection_strategy: str = "unknown"
    write_response_preference: Optional[bool] = None
    write_service_uuid: str = ""
    write_char_uuid: str = ""
    bulk_write_char_uuid: str = ""
    notify_char_uuid: str = ""


@dataclass
class _NotificationWaiter:
    label: str
    match: Callable[[bytes], bool]
    future: asyncio.Future


class _BleakTransportSession:
    """Encapsulates endpoint binding and delegates family runtime to controllers."""

    def __init__(
        self,
        transport_profile: BleTransportProfile,
        write_resolver: _BleWriteEndpointResolver,
        reporter: reporting.Reporter,
    ) -> None:
        self._transport_profile = transport_profile
        self._write_resolver = write_resolver
        self._reporter = reporter
        self.bindings = _BleakBindings()
        self.notify_started = False
        self._flow_can_write = True
        self._flow_resume_event: asyncio.Event | None = None
        self._flow_resume_event_loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._runtime_controller = None
        self._notification_waiters: list[_NotificationWaiter] = []
        self._notification_history: deque[tuple[float, bytes]] = deque(
            maxlen=_NOTIFICATION_HISTORY_LIMIT
        )
        self._notification_count = 0
        self._flow_pause_count = 0
        self._flow_resume_count = 0
        self._session_started_monotonic: float | None = None
        self._last_write_monotonic: float | None = None
        self._last_write_label: str | None = None
        self._last_bulk_write_monotonic: float | None = None
        self._last_notification_monotonic: float | None = None
        self._write_chunk_count = 0
        self._write_byte_count = 0

    @property
    def flow_can_write(self) -> bool:
        return self._flow_can_write

    @flow_can_write.setter
    def flow_can_write(self, value: bool) -> None:
        self._flow_can_write = bool(value)
        if self._flow_resume_event is None:
            return
        if self._flow_can_write:
            self._flow_resume_event.set()
        else:
            self._flow_resume_event.clear()

    def apply_write_selection(self, selection: _WriteSelection) -> None:
        self.bindings.write_char = selection.char
        self.bindings.write_selection_strategy = selection.strategy
        self.bindings.write_response_preference = selection.response_preference
        self.bindings.write_service_uuid = selection.service_uuid
        self.bindings.write_char_uuid = selection.char_uuid
        self.report_debug(
            "selected write characteristic "
            f"service={self.bindings.write_service_uuid} char={self.bindings.write_char_uuid} "
            f"strategy={self.bindings.write_selection_strategy} "
            f"response_preference={self.bindings.write_response_preference}"
        )

    def configure_endpoints(self, services: Iterable[object]) -> None:
        transport = self._transport_profile

        self.bindings.bulk_write_char = None
        self.bindings.bulk_write_char_uuid = ""
        bulk_write = transport.bulk_write
        if bulk_write is not None:
            self.bindings.bulk_write_char = self._find_characteristic_by_uuid(
                services,
                bulk_write.char_uuid,
                preferred_service_uuid=transport.preferred_service_uuid,
            )
            self.bindings.bulk_write_char_uuid = _BleWriteEndpointResolver._normalize_uuid(
                getattr(self.bindings.bulk_write_char, "uuid", "")
            )
            if self.bindings.bulk_write_char:
                self.report_debug(
                    f"selected bulk characteristic char={self.bindings.bulk_write_char_uuid}"
                )
            else:
                self.report_debug("configured bulk characteristic not found")

        self.bindings.notify_char = None
        self.bindings.notify_char_uuid = ""
        if transport.notify_char_uuid:
            self.bindings.notify_char = self._find_characteristic_by_uuid(
                services,
                transport.notify_char_uuid,
                preferred_service_uuid=transport.preferred_service_uuid,
            )
        elif transport.prefer_generic_notify:
            self.bindings.notify_char = self.find_notify_characteristic(services)

        self.bindings.notify_char_uuid = _BleWriteEndpointResolver._normalize_uuid(
            getattr(self.bindings.notify_char, "uuid", "")
        )
        if self.bindings.notify_char:
            self.report_debug(
                f"selected notify characteristic char={self.bindings.notify_char_uuid}"
            )
        elif transport.notify_char_uuid or transport.prefer_generic_notify:
            self.report_debug("configured notify characteristic not found")

    async def attach_runtime_controller(
        self,
        runtime_controller: Any | None,
        *,
        mtu_size: int,
        timeout: float,
    ):
        if runtime_controller is None:
            return None
        if runtime_controller is not self._runtime_controller:
            previous = self._runtime_controller
            runtime_controller.adopt_previous(previous)
            if previous is None:
                await runtime_controller.initialize_connection(
                    self,
                    mtu_size=mtu_size,
                    timeout=timeout,
                )
                self._runtime_controller = runtime_controller
                self._replay_notifications_to_runtime_controller()
                await runtime_controller.after_initialize(self, timeout=timeout)
            else:
                self._runtime_controller = runtime_controller

    async def start_notify_if_available(self, client: Any, callback) -> None:
        if not self.bindings.notify_char or not self.bindings.notify_char_uuid:
            return
        start_notify = getattr(client, "start_notify", None)
        if not callable(start_notify):
            return
        await start_notify(self.bindings.notify_char_uuid, callback)
        self.notify_started = True
        self.report_debug(
            f"subscribed to notify characteristic {self.bindings.notify_char_uuid}"
        )

    async def stop_notify_if_started(self, client: Any) -> None:
        if self._runtime_controller is not None:
            await self._runtime_controller.stop(self)
        self._cancel_notification_waiters()
        if not self.notify_started or not self.bindings.notify_char_uuid:
            return
        stop_notify = getattr(client, "stop_notify", None)
        if not callable(stop_notify):
            return
        try:
            await stop_notify(self.bindings.notify_char_uuid)
        except Exception:
            pass
        self.notify_started = False

    async def initialize_connection(
        self,
        client: Any,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        self._client = client
        self._session_started_monotonic = time.monotonic()
        if self._runtime_controller is not None:
            await self._runtime_controller.initialize_connection(self, mtu_size=mtu_size, timeout=timeout)
            await self._runtime_controller.after_initialize(self, timeout=timeout)

    async def send(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        self._client = client
        if not self.bindings.write_char:
            raise RuntimeError("No write characteristic available")

        await self._send_standard(client, data, mtu_size=mtu_size, timeout=timeout)

    async def _send_standard(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        mtu_payload = self._effective_mtu_payload(
            self.bindings.write_char,
            mtu_size,
            response=response,
            reserve=self._transport_profile.write_without_response_payload_reserve,
        )
        chunk_size = min(mtu_payload, self._transport_profile.standard_chunk_cap)
        delay_seconds = self._transport_profile.standard_write_delay_ms / 1000.0
        chunk_count = (len(data) + chunk_size - 1) // chunk_size if chunk_size else 0
        report_ble_write_plan(
            self._reporter,
            response=response,
            strategy=self.bindings.write_selection_strategy,
            char_uuid=self.bindings.write_char_uuid,
            payload_bytes=len(data),
            mtu_payload=mtu_payload,
            chunk_size=chunk_size,
            chunk_count=chunk_count,
            reserve=self._transport_profile.write_without_response_payload_reserve,
            delay_ms=self._transport_profile.standard_write_delay_ms,
            payload_head=data[:16],
            payload_tail=data[-16:],
        )
        counters_before = self._write_counters()
        started = time.monotonic()
        chunks_written = await self._write_chunks(
            client,
            self.bindings.write_char,
            data,
            response=response,
            chunk_size=chunk_size,
            delay_seconds=delay_seconds,
            timeout=timeout,
            wait_for_flow=self._transport_profile.flow_controlled_standard_write,
            progress_label="standard",
            total_chunks=chunk_count,
        )
        report_ble_write_summary(
            self._reporter,
            "standard write done",
            byte_count=len(data),
            chunks_written=chunks_written,
            elapsed_seconds=time.monotonic() - started,
            before=counters_before,
            after=self._write_counters(),
        )

    async def _write_bulk_payload(
        self,
        client: Any,
        data: bytes,
        *,
        bulk_write: BleBulkWriteProfile,
        mtu_size: int,
        timeout: float,
    ) -> None:
        bulk_response = self._resolve_response_mode(
            self.bindings.bulk_write_char,
            "preferred_uuid",
            False,
        )
        bulk_mtu_payload = self._effective_mtu_payload(
            self.bindings.bulk_write_char,
            mtu_size,
            response=bulk_response,
            reserve=self._transport_profile.write_without_response_payload_reserve,
        )
        bulk_chunk_size = min(bulk_mtu_payload, bulk_write.chunk_cap)
        bulk_chunk_count = (
            (len(data) + bulk_chunk_size - 1) // bulk_chunk_size
            if bulk_chunk_size
            else 0
        )
        report_ble_split_bulk_plan(
            self._reporter,
            response=bulk_response,
            payload_bytes=len(data),
            mtu_payload=bulk_mtu_payload,
            chunk_size=bulk_chunk_size,
            chunk_count=bulk_chunk_count,
            reserve=self._transport_profile.write_without_response_payload_reserve,
            delay_ms=bulk_write.write_delay_ms,
            flow_control=bulk_write.flow_controlled,
        )
        counters_before = self._write_counters()
        started = time.monotonic()
        chunks_written = await self._write_chunks(
            client,
            self.bindings.bulk_write_char,
            data,
            response=bulk_response,
            chunk_size=bulk_chunk_size,
            delay_seconds=bulk_write.write_delay_ms / 1000.0,
            timeout=timeout,
            wait_for_flow=bulk_write.flow_controlled,
            progress_label="split bulk",
            total_chunks=bulk_chunk_count,
        )
        report_ble_write_summary(
            self._reporter,
            "split bulk done",
            byte_count=len(data),
            chunks_written=chunks_written,
            elapsed_seconds=time.monotonic() - started,
            before=counters_before,
            after=self._write_counters(),
        )

    async def _write_chunks(
        self,
        client: Any,
        char: Any,
        data: bytes,
        *,
        response: bool,
        chunk_size: int,
        delay_seconds: float,
        timeout: float,
        wait_for_flow: bool = False,
        progress_label: str | None = None,
        total_chunks: int | None = None,
    ) -> int:
        if chunk_size <= 0:
            raise ValueError("BLE chunk size must be positive")
        progress = BleWriteProgress(progress_label, total_chunks) if progress_label else None
        chunks_written = 0
        for chunk_index, offset in enumerate(range(0, len(data), chunk_size), start=1):
            if wait_for_flow:
                await self._wait_for_flow(timeout)
            chunk = data[offset : offset + chunk_size]
            await client.write_gatt_char(char, chunk, response=response)
            self._record_write_activity(progress_label, len(chunk))
            chunks_written = chunk_index
            byte_count = min(offset + chunk_size, len(data))
            progress_message = None if progress is None else progress.message_for(
                chunk_index,
                byte_count,
                len(data),
            )
            if progress_message:
                self.report_debug(progress_message)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
        return chunks_written

    async def _wait_for_flow(self, timeout: float) -> None:
        if self.flow_can_write:
            return
        try:
            await asyncio.wait_for(
                self._flow_resume_event_for_current_loop().wait(),
                timeout=max(0.0, timeout),
            )
        except TimeoutError:
            raise TimeoutError("Timed out waiting for BLE flow-control resume") from None

    def handle_notification(self, payload: bytes) -> None:
        self._notification_count += 1
        now = time.monotonic()
        self._last_notification_monotonic = now
        self._remember_notification(now, payload)
        self._match_notification_waiters(payload)
        if self._runtime_controller is not None:
            self._runtime_controller.handle_notification(self, payload)
        self.report_debug(f"BLE notify: {payload.hex()}")

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        self.flow_can_write = not paused
        if paused:
            self._flow_pause_count += 1
            label = "flow pause"
        else:
            self._flow_resume_count += 1
            label = "flow resume"
        detail = "" if not payload else f": {payload.hex()}"
        self.report_debug(label + detail)

    @staticmethod
    def _find_characteristic_by_uuid(
        services: Iterable[object],
        char_uuid: str,
        *,
        preferred_service_uuid: str = "",
    ) -> Optional[Any]:
        target = _BleWriteEndpointResolver._normalize_uuid(char_uuid)
        preferred_service = _BleWriteEndpointResolver._normalize_uuid(preferred_service_uuid)
        if preferred_service:
            for service in services:
                service_uuid = _BleWriteEndpointResolver._normalize_uuid(getattr(service, "uuid", ""))
                if service_uuid != preferred_service:
                    continue
                for characteristic in getattr(service, "characteristics", []):
                    if _BleWriteEndpointResolver._normalize_uuid(getattr(characteristic, "uuid", "")) == target:
                        return characteristic
        for service in services:
            for characteristic in getattr(service, "characteristics", []):
                if _BleWriteEndpointResolver._normalize_uuid(getattr(characteristic, "uuid", "")) == target:
                    return characteristic
        return None

    @classmethod
    def find_notify_characteristic(cls, services: Iterable[object]) -> Optional[Any]:
        preferred: List[Tuple[str, str, Any]] = []
        generic: List[Tuple[str, str, Any]] = []
        for service in services:
            service_uuid = _BleWriteEndpointResolver._normalize_uuid(getattr(service, "uuid", ""))
            for characteristic in getattr(service, "characteristics", []):
                props = {str(item).strip().lower() for item in getattr(characteristic, "properties", [])}
                if "notify" not in props and "indicate" not in props:
                    continue
                char_uuid = _BleWriteEndpointResolver._normalize_uuid(getattr(characteristic, "uuid", ""))
                candidate = (service_uuid, char_uuid, characteristic)
                if _BleWriteEndpointResolver._uuid_is_preferred(
                    char_uuid,
                    _BleWriteEndpointResolver._PREFERRED_NOTIFY_UUIDS,
                    _BleWriteEndpointResolver._PREFERRED_NOTIFY_SHORT,
                ):
                    preferred.append(candidate)
                else:
                    generic.append(candidate)
        candidates = sorted(preferred or generic, key=lambda item: (item[0], item[1]))
        return candidates[0][2] if candidates else None

    def _resolve_response_mode(
        self,
        characteristic: Any,
        strategy: str,
        response_preference: Optional[bool],
    ) -> bool:
        return self._write_resolver.resolve_response_mode(
            getattr(characteristic, "properties", []),
            strategy,
            response_preference,
        )

    @staticmethod
    def _effective_mtu_payload(
        characteristic: Any,
        fallback: int,
        *,
        response: bool,
        reserve: int = 0,
    ) -> int:
        if response:
            return fallback
        payload = fallback
        try:
            max_without_response = getattr(
                characteristic,
                "max_write_without_response_size",
                None,
            )
        except Exception:
            max_without_response = None
        if isinstance(max_without_response, int) and max_without_response > 0:
            payload = min(max_without_response, 512)
        if reserve > 0:
            payload -= reserve
        return max(1, payload)

    def report_debug(self, message: str) -> None:
        self._reporter.debug(short="BLE", detail=message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self._reporter.warning(short=short, detail=detail)

    def report_disconnect_diagnostics(self) -> None:
        now = time.monotonic()
        report_ble_disconnect_state(
            self._reporter,
            connected_seconds=self._elapsed_since(now, self._session_started_monotonic),
            notify_started=self.notify_started,
            notifications=self._notification_count,
            pending_waiters=len(self._notification_waiters),
            write_chunks=self._write_chunk_count,
            write_bytes=self._write_byte_count,
            last_write_label=self._last_write_label,
            since_last_write_seconds=self._elapsed_since(now, self._last_write_monotonic),
            since_last_bulk_write_seconds=self._elapsed_since(now, self._last_bulk_write_monotonic),
            since_last_notify_seconds=self._elapsed_since(now, self._last_notification_monotonic),
            flow_pauses=self._flow_pause_count,
            flow_resumes=self._flow_resume_count,
        )

    def _record_write_activity(self, label: str | None, byte_count: int) -> None:
        now = time.monotonic()
        self._last_write_monotonic = now
        self._last_write_label = label or "control"
        self._write_chunk_count += 1
        self._write_byte_count += byte_count
        if label == "split bulk":
            self._last_bulk_write_monotonic = now

    @staticmethod
    def _elapsed_since(now: float, then: float | None) -> float | None:
        if then is None:
            return None
        return max(0.0, now - then)

    def _write_counters(self) -> BleWriteCounters:
        return BleWriteCounters(
            notifications=self._notification_count,
            flow_pauses=self._flow_pause_count,
            flow_resumes=self._flow_resume_count,
        )

    def can_send_control_packet(self) -> bool:
        return bool(self._client and self.bindings.write_char)

    def can_send_bulk_payload(self) -> bool:
        return bool(
            self._client
            and self.bindings.bulk_write_char
            and self._transport_profile.bulk_write is not None
        )

    def can_query_control_packet(self) -> bool:
        return False

    def can_wait_for_notification(self) -> bool:
        return self.notify_started

    def can_send_control_packet_wait_notification(self) -> bool:
        return self.can_send_control_packet() and self.can_wait_for_notification()

    async def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        if not self.can_wait_for_notification():
            if required:
                raise RuntimeError(f"BLE notification wait unavailable: {label}")
            self.report_debug(f"optional notification wait unavailable: {label}")
            return None
        # Some protocols send an ACK immediately after the last data write; keep
        # passive waits from missing a notification that arrived just before the waiter.
        historical = self._match_notification_history(match)
        if historical is not None:
            self.report_debug(f"notification matched recent {label}: {historical.hex()}")
            return historical
        waiter = self._register_notification_waiter(label, match)
        try:
            return await self._wait_for_registered_notification(
                waiter,
                timeout=timeout,
                required=required,
            )
        finally:
            self._remove_notification_waiter(waiter)

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match: Callable[[bytes], bool],
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        if not self.can_send_control_packet_wait_notification():
            if required:
                raise RuntimeError(f"BLE notification query unavailable: {label}")
            self.report_debug(f"optional notification query unavailable: {label}")
            return None
        waiter = self._register_notification_waiter(label, match)
        try:
            sent = await self.send_control_packet(packet, timeout=timeout)
            if not sent:
                if required:
                    raise RuntimeError(f"BLE control send failed before notification wait: {label}")
                return None
            return await self._wait_for_registered_notification(
                waiter,
                timeout=timeout,
                required=required,
            )
        finally:
            self._remove_notification_waiter(waiter)

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        if not self.can_send_control_packet():
            return False
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        await self._write_chunks(
            self._client,
            self.bindings.write_char,
            packet,
            response=response,
            chunk_size=min(180, self._transport_profile.standard_chunk_cap),
            delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
            timeout=timeout,
        )
        return True

    async def send_bulk_payload(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float = 1.0,
    ) -> bool:
        self._client = client
        bulk_write = self._transport_profile.bulk_write
        if not self.can_send_bulk_payload() or bulk_write is None:
            return False
        await self._write_bulk_payload(
            client,
            data,
            bulk_write=bulk_write,
            mtu_size=mtu_size,
            timeout=timeout,
        )
        return True

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        _ = packet, timeout, reply_complete
        return None

    def _register_notification_waiter(
        self,
        label: str,
        match: Callable[[bytes], bool],
    ) -> _NotificationWaiter:
        loop = asyncio.get_running_loop()
        waiter = _NotificationWaiter(
            label=label,
            match=match,
            future=loop.create_future(),
        )
        self._notification_waiters.append(waiter)
        return waiter

    def _remember_notification(self, now: float, payload: bytes) -> None:
        self._notification_history.append((now, bytes(payload)))
        self._trim_notification_history(now)

    def _replay_notifications_to_runtime_controller(self) -> None:
        if self._runtime_controller is None:
            return
        now = time.monotonic()
        self._trim_notification_history(now)
        for _timestamp, payload in self._notification_history:
            self._runtime_controller.handle_notification(self, payload)

    def _match_notification_history(self, match: Callable[[bytes], bool]) -> bytes | None:
        now = time.monotonic()
        self._trim_notification_history(now)
        for _timestamp, payload in reversed(self._notification_history):
            if match(payload):
                return payload
        return None

    def _trim_notification_history(self, now: float) -> None:
        while (
            self._notification_history
            and now - self._notification_history[0][0] > _NOTIFICATION_HISTORY_TTL_SEC
        ):
            self._notification_history.popleft()

    async def _wait_for_registered_notification(
        self,
        waiter: _NotificationWaiter,
        *,
        timeout: float,
        required: bool,
    ) -> bytes | None:
        try:
            return await asyncio.wait_for(waiter.future, timeout=max(0.0, timeout))
        except TimeoutError:
            if required:
                raise TimeoutError(f"Timed out waiting for BLE notification: {waiter.label}") from None
            self.report_debug(f"optional notification wait timed out: {waiter.label}")
            return None

    def _match_notification_waiters(self, payload: bytes) -> None:
        for waiter in tuple(self._notification_waiters):
            if waiter.future.done():
                self._remove_notification_waiter(waiter)
                continue
            try:
                matched = waiter.match(payload)
            except Exception as exc:
                waiter.future.set_exception(exc)
                self._remove_notification_waiter(waiter)
                continue
            if matched:
                waiter.future.set_result(payload)
                self._remove_notification_waiter(waiter)
                self.report_debug(
                    f"notification matched {waiter.label}: {payload.hex()}"
                )

    def _remove_notification_waiter(self, waiter: _NotificationWaiter) -> None:
        try:
            self._notification_waiters.remove(waiter)
        except ValueError:
            pass

    def _cancel_notification_waiters(self) -> None:
        waiters = tuple(self._notification_waiters)
        self._notification_waiters.clear()
        for waiter in waiters:
            if not waiter.future.done():
                waiter.future.cancel()

    def _flow_resume_event_for_current_loop(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        if self._flow_resume_event is None or self._flow_resume_event_loop is not loop:
            self._flow_resume_event = asyncio.Event()
            self._flow_resume_event_loop = loop
            if self.flow_can_write:
                self._flow_resume_event.set()
        return self._flow_resume_event
