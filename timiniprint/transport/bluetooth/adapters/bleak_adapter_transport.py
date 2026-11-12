"""Family-agnostic BLE transport helpers for the bleak adapter."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from .... import reporting
from ....devices.bluetooth_channels import BLE_UART_READERS, ble_uuid
from ....devices.bluetooth_profiles import BleBulkWriteProfile, BleGattLayout, BleTransportProfile
from ...gatt import is_gatt_metadata_service
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
_MAX_STARTUP_NOTIFICATION_BYTES = 64 * 1024
_MAX_STARTUP_NOTIFICATIONS = 1024


@dataclass
class _BleakBindings:
    write_char: Any = None
    bulk_write_char: Any = None
    notify_char: Any = None
    additional_notify_chars: tuple[Any, ...] = ()
    write_selection_strategy: str = "unknown"
    write_response_preference: Optional[bool] = None
    write_service_uuid: str = ""
    write_char_uuid: str = ""
    bulk_write_char_uuid: str = ""
    gatt_layout: BleGattLayout | None = None
    gatt_service: Any = None

    @property
    def notify_char_uuid(self) -> str:
        return _BleWriteEndpointResolver._normalize_uuid(getattr(self.notify_char, "uuid", ""))

    @property
    def additional_notify_char_uuids(self) -> tuple[str, ...]:
        return tuple(_BleWriteEndpointResolver._normalize_uuid(char.uuid) for char in self.additional_notify_chars)


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
        self._notify_subscriptions: dict[tuple[str, int], Any] = {}
        self._flow_can_write = True
        self._flow_resume_event: asyncio.Event | None = None
        self._flow_resume_event_loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._mtu_size = 20
        self._runtime_controller = None
        self._connection_error: ConnectionError | None = None
        self._disconnected_runtime_task: asyncio.Task | None = None
        self._runtime_attached_once = False
        self._startup_notifications: list[tuple[bytes, bool, str]] = []
        self._startup_notification_bytes = 0
        self._startup_notification_error: Exception | None = None
        self._notification_waiters: list[_NotificationWaiter] = []
        self._notification_history: deque[tuple[float, bytes, bool, str]] = deque(
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

    def is_ble_transport(self) -> bool:
        return True

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
        self.bindings.gatt_layout = selection.layout
        self.bindings.gatt_service = selection.service
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
        services = tuple(services)
        transport = self._transport_profile

        layout = self.bindings.gatt_layout
        if transport.gatt_layouts:
            if layout is None:
                selection = self._write_resolver.resolve(
                    services, gatt_layouts=transport.gatt_layouts,
                    required_write_char_uuid=transport.required_write_char_uuid,
                )
                if selection is None:
                    raise RuntimeError("No complete configured GATT layout was found")
                self.apply_write_selection(selection)
                layout = selection.layout
            services = (self.bindings.gatt_service,)

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
        self.bindings.additional_notify_chars = ()
        notify_services = (tuple(str(service.uuid) for service in services)
                           if transport.notify_all_services else transport.notify_service_uuids)
        service_notify = self._service_notify_characteristics(
            services, notify_services, preserve_instances=transport.notify_all_services,
        )
        if layout:
            self.bindings.notify_char = self._find_characteristic_by_uuid(services, layout.notify_char_uuid)
        elif transport.notify_service_uuids or transport.notify_all_services:
            self.bindings.notify_char = service_notify[0] if service_notify else None
        elif transport.notify_char_uuid:
            self.bindings.notify_char = self._find_characteristic_by_uuid(
                services, transport.notify_char_uuid,
                preferred_service_uuid=transport.preferred_service_uuid,
            )
        elif transport.prefer_generic_notify:
            self.bindings.notify_char = self.find_notify_characteristic(services)

        # A broad subscription retains distinct native instances. Existing
        # explicit/service-scoped bindings retain their UUID de-duplication.
        if transport.notify_all_services:
            additional = {id(char): char for char in service_notify if char is not self.bindings.notify_char}
        else:
            additional = {str(char.uuid).lower(): char for char in service_notify
                          if str(char.uuid).lower() != self.bindings.notify_char_uuid}
        for uuid in (transport.control_notify_char_uuid,):
            if not uuid:
                continue
            char = self._find_characteristic_by_uuid(
                services, uuid, preferred_service_uuid=transport.preferred_service_uuid,
            )
            if char is None:
                continue
            if layout and not {"notify", "indicate"}.intersection(
                self._write_resolver._normalize_properties(getattr(char, "properties", ())),
            ):
                continue
            if transport.notify_all_services:
                if char is not self.bindings.notify_char:
                    additional[id(char)] = char
            elif str(char.uuid).lower() != self.bindings.notify_char_uuid:
                additional[str(char.uuid).lower()] = char
        self.bindings.additional_notify_chars = tuple(additional.values())
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
        if runtime_controller is not None:
            self._raise_connection_error()
        pending = self._disconnected_runtime_task
        if pending is not None:
            try:
                await asyncio.shield(pending)
            finally:
                if pending.done():
                    self._disconnected_runtime_task = None
        previous = self._runtime_controller
        if runtime_controller is previous:
            return
        await self._stop_runtime_controller()
        if runtime_controller is None:
            return
        self._raise_connection_error()
        if previous is None:
            try:
                if self._startup_notification_error is not None:
                    raise self._startup_notification_error
                self._mtu_size = mtu_size
                await runtime_controller.initialize_connection(
                    self,
                    mtu_size=mtu_size,
                    timeout=timeout,
                )
                self._raise_connection_error()
                self._runtime_controller = runtime_controller
                self._runtime_attached_once = True
                self._replay_notifications_to_runtime_controller()
                await runtime_controller.after_initialize(self, timeout=timeout)
                self._raise_connection_error()
            except BaseException:
                if self._runtime_controller is None and self._disconnected_runtime_task is None:
                    # Retain provisional resources too if stopping them fails.
                    self._runtime_controller = runtime_controller
                try:
                    await self.attach_runtime_controller(None, mtu_size=0, timeout=timeout)
                except Exception as exc:
                    self.report_warning(short="Runtime cleanup failed", detail=str(exc))
                raise
        else:
            self._runtime_controller = runtime_controller

    async def _stop_runtime_controller(self) -> None:
        """One stop path for replacement, initialization rollback and disconnect."""
        controller = self._runtime_controller
        if controller is None:
            return
        self._runtime_controller = None
        try:
            await controller.stop(self)
        except BaseException:
            self._runtime_controller = controller
            raise

    @staticmethod
    def _service_notify_characteristics(services, service_uuids, *, preserve_instances=False) -> tuple[Any, ...]:
        allowed = {uuid.lower() for uuid in service_uuids}
        found = {}
        for service in services:
            if str(getattr(service, "uuid", "")).lower() not in allowed:
                continue
            for char in getattr(service, "characteristics", ()):
                props = {str(value).lower() for value in getattr(char, "properties", ())}
                if props.intersection({"notify", "indicate"}):
                    key = (str(char.uuid).lower(), len(found)) if preserve_instances else str(char.uuid).lower()
                    found[key] = char
        return tuple(found[uuid] for uuid in sorted(found))

    async def start_notify_if_available(self, client: Any, callback) -> None:
        characteristics = tuple(char for char in (
            self.bindings.notify_char, *self.bindings.additional_notify_chars,
        ) if getattr(char, "uuid", ""))
        start_notify = getattr(client, "start_notify", None)
        if not characteristics or not callable(start_notify):
            return
        subscribed = []
        for index, char in enumerate(characteristics):
            uuid = _BleWriteEndpointResolver._normalize_uuid(char.uuid)
            if self._transport_profile.notify_all_services:
                target = source = char
            elif self.bindings.gatt_layout:
                target = char
                source = uuid
            else:
                target = source = uuid

            metadata_only = self._transport_profile.notify_all_services and is_gatt_metadata_service(
                getattr(char, "service_uuid", ""),
            )

            def channel_callback(_sender, data, *, _source=source, _metadata_only=metadata_only, _uuid=uuid):
                if _metadata_only:
                    self.report_debug(f"GATT metadata notification: char={_uuid} bytes={len(data)}")
                    self.report_trace(f"GATT metadata notify {_uuid}: {bytes(data).hex()}")
                    return
                callback(_source, data)

            try:
                await start_notify(target, channel_callback)
            except Exception:
                if (index == 0 or self._transport_profile.notify_all_services
                        or self._transport_profile.notify_service_uuids or self._is_control_channel(uuid)):
                    await self.stop_notify_if_started(client)
                    raise
                self.report_debug(f"optional notify subscription failed for {uuid}")
                continue
            subscribed.append(uuid)
            self._notify_subscriptions[(uuid, index)] = target
            self.notify_started = True
        self.report_debug(f"subscribed to notify characteristics {subscribed}")

    def connection_lost(self) -> None:
        """Release blocked I/O in the BLE loop, without reconnecting or retrying.

        The stop task is owned here and drained before disposing the I/O loop.
        """
        if self._connection_error is not None:
            return
        self._connection_error = ConnectionError("BLE connection was lost")
        self.report_debug("BLE native connection closed")
        if self._flow_resume_event is not None:
            self._flow_resume_event.set()
        for waiter in tuple(self._notification_waiters):
            if not waiter.future.done():
                waiter.future.set_exception(self._connection_error)
            self._remove_notification_waiter(waiter)
        if self._runtime_controller is not None:
            self._disconnected_runtime_task = asyncio.create_task(self._stop_runtime_controller())
            # Retain the task for cleanup without an unobserved exception if
            # the caller delays closing the disconnected link.
            self._disconnected_runtime_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception(),
            )

    def _raise_connection_error(self) -> None:
        if self._connection_error is not None:
            raise self._connection_error

    def ensure_runtime_stopped(self) -> None:
        """Do not release native resources while runtime cleanup is incomplete."""
        if self._runtime_controller is not None or self._disconnected_runtime_task is not None:
            raise RuntimeError("BLE cleanup incomplete: runtime shutdown has not finished")

    async def stop_notify_if_started(self, client: Any) -> None:
        await self.attach_runtime_controller(None, mtu_size=0, timeout=0)
        self._cancel_notification_waiters()
        self._startup_notifications.clear()
        self._startup_notification_bytes = 0
        if not self.notify_started:
            return
        stop_notify = getattr(client, "stop_notify", None)
        if not callable(stop_notify):
            return
        for (uuid, _index), target in tuple(self._notify_subscriptions.items()):
            try:
                await stop_notify(target)
            except Exception as exc:
                self.report_warning(short="BLE notification shutdown failed", detail=f"{uuid}: {exc}")
        self._notify_subscriptions.clear()
        self.notify_started = False

    async def initialize_connection(
        self,
        client: Any,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        self._client = client
        self._mtu_size = mtu_size
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
        offset = 0
        while offset < len(data):
            self._raise_connection_error()
            if wait_for_flow:
                await self._wait_for_flow(timeout)
            size = min(chunk_size, len(data) - offset)
            if self._runtime_controller is not None:
                permitted = await self._runtime_controller.before_write(self, size=size, timeout=timeout)
                if permitted is not None:
                    if type(permitted) is not int or not 0 < permitted <= size:
                        raise ValueError("Runtime write size must be a positive integer within the host limit")
                    if permitted < size:
                        # A negotiated size may change again while waiting for
                        # credits. Do not present an obsolete chunk-count total.
                        progress = None
                    size = permitted
            chunk = data[offset : offset + size]
            self._raise_connection_error()
            await client.write_gatt_char(char, chunk, response=response)
            self._raise_connection_error()
            self._record_write_activity(progress_label, len(chunk))
            chunks_written += 1
            offset += size
            progress_message = None if progress is None else progress.message_for(
                chunks_written,
                offset,
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
        resume_timeout = self._transport_profile.flow_resume_timeout_s
        if resume_timeout is None:
            resume_timeout = timeout
        try:
            await asyncio.wait_for(
                self._flow_resume_event_for_current_loop().wait(),
                timeout=max(0.0, resume_timeout),
            )
            self._raise_connection_error()
        except asyncio.TimeoutError:
            raise TimeoutError("Timed out waiting for BLE flow-control resume") from None

    def handle_notification(self, payload: bytes, source: Any = None) -> None:
        self._notification_count += 1
        now = time.monotonic()
        self._last_notification_monotonic = now
        source_value = getattr(source, "uuid", source)
        source_uuid = _BleWriteEndpointResolver._normalize_uuid(source_value) if isinstance(source_value, str) else ""
        is_control = self._is_control_channel(source_uuid)
        channel = source_uuid if isinstance(source, (str, type(None))) else (
            f"{source_uuid}#{getattr(source, 'handle', id(source))}"
        )
        self._remember_notification(now, payload, is_control, channel)
        try:
            if self._runtime_controller is not None:
                if is_control:
                    self._runtime_controller.handle_control_notification(self, payload)
                else:
                    self._runtime_controller.handle_ble_notification(self, payload, source=channel)
        finally:
            if not is_control:
                self._match_notification_waiters(payload)
        self.report_trace(f"BLE notify: {payload.hex()}")

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        self.flow_can_write = not paused
        if paused:
            self._flow_pause_count += 1
            label = "flow pause"
        else:
            self._flow_resume_count += 1
            label = "flow resume"
        self.report_debug(label)
        if payload:
            self.report_trace(f"{label}: {payload.hex()}")

    def _is_control_channel(self, uuid: str) -> bool:
        configured = self._transport_profile.control_notify_char_uuid
        return bool(configured) and uuid == _BleWriteEndpointResolver._normalize_uuid(configured)

    def can_observe_control_notifications(self) -> bool:
        return any(self._is_control_channel(uuid) for uuid, _index in self._notify_subscriptions)

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
                if ble_uuid(char_uuid) in BLE_UART_READERS:
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

    def report_trace(self, message: str) -> None:
        self._reporter.trace(short="BLE", detail=message)

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
        return self._connection_error is None and bool(self._client and self.bindings.write_char)

    def can_send_bulk_payload(self) -> bool:
        return bool(
            self._client
            and self.bindings.bulk_write_char
            and self._transport_profile.bulk_write is not None
        )

    def can_query_control_packet(self) -> bool:
        return False

    def can_wait_for_notification(self) -> bool:
        return self._connection_error is None and self.notify_started

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
        self._raise_connection_error()
        if not self.can_wait_for_notification():
            if required:
                raise RuntimeError(f"BLE notification wait unavailable: {label}")
            self.report_debug(f"optional notification wait unavailable: {label}")
            return None
        # Some protocols send an ACK immediately after the last data write; keep
        # passive waits from missing a notification that arrived just before the waiter.
        historical = self._match_notification_history(match)
        if historical is not None:
            self.report_debug(f"notification matched recent {label}")
            self.report_trace(f"notification matched recent {label}: {historical.hex()}")
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
            chunk_size=min(
                180, self._transport_profile.standard_chunk_cap,
                self._effective_mtu_payload(
                    self.bindings.write_char, self._mtu_size, response=response,
                    reserve=self._transport_profile.write_without_response_payload_reserve,
                ),
            ),
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

    def _remember_notification(self, now: float, payload: bytes, is_control: bool, channel: str) -> None:
        # Initial grants and partial metadata are not expendable recent-reply
        # history. Keep them until the first controller owns reception.
        if payload and not self._runtime_attached_once and self._startup_notification_error is None:
            self._startup_notification_bytes += len(payload)
            if (self._startup_notification_bytes > _MAX_STARTUP_NOTIFICATION_BYTES
                    or len(self._startup_notifications) >= _MAX_STARTUP_NOTIFICATIONS):
                self._startup_notification_error = RuntimeError("BLE startup notifications exceeded the replay limit")
                self._startup_notifications.clear()
            else:
                self._startup_notifications.append((bytes(payload), is_control, channel))
        self._notification_history.append((now, bytes(payload), is_control, channel))
        self._trim_notification_history(now)

    def _replay_notifications_to_runtime_controller(self) -> None:
        if self._runtime_controller is None:
            return
        pending, self._startup_notifications = self._startup_notifications, []
        self._startup_notification_bytes = 0
        for payload, is_control, channel in pending:
            if is_control:
                self._runtime_controller.handle_control_notification(self, payload)
            else:
                self._runtime_controller.handle_ble_notification(self, payload, source=channel)

    def _match_notification_history(self, match: Callable[[bytes], bool]) -> bytes | None:
        now = time.monotonic()
        self._trim_notification_history(now)
        for _timestamp, payload, is_control, _channel in reversed(self._notification_history):
            if not is_control and match(payload):
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
        except asyncio.TimeoutError:
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
                self.report_debug(f"notification matched {waiter.label}")
                self.report_trace(f"notification matched {waiter.label}: {payload.hex()}")

    def _remove_notification_waiter(self, waiter: _NotificationWaiter) -> None:
        if waiter.future.done() and not waiter.future.cancelled():
            waiter.future.exception()
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
