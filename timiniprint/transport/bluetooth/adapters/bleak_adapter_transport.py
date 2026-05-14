"""Family-agnostic BLE transport helpers for the bleak adapter."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from .... import reporting
from ....printing.runtime.base import RuntimeController
from ....printing.runtime.factory import _runtime_controller_for_family
from ....protocol.families import BleBulkWriteProfile, BleTransportProfile, split_prefixed_bulk_stream
from ....protocol.family import ProtocolFamily
from ....protocol.packet import make_packet, prefixed_packet_length
from .bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver, _WriteSelection


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
        protocol_family: ProtocolFamily,
        transport_profile: BleTransportProfile,
        write_resolver: _BleWriteEndpointResolver,
        reporter: reporting.Reporter,
    ) -> None:
        self._protocol_family = protocol_family
        self._transport_profile = transport_profile
        self._write_resolver = write_resolver
        self._reporter = reporter
        self.bindings = _BleakBindings()
        self.notify_started = False
        self._flow_can_write = True
        self._flow_resume_event: asyncio.Event | None = None
        self._flow_resume_event_loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._runtime_controller = _runtime_controller_for_family(protocol_family)
        self._notification_waiters: list[_NotificationWaiter] = []

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
        elif transport.prefer_generic_notify or transport.flow_control is not None:
            self.bindings.notify_char = self.find_notify_characteristic(services)

        self.bindings.notify_char_uuid = _BleWriteEndpointResolver._normalize_uuid(
            getattr(self.bindings.notify_char, "uuid", "")
        )
        if self.bindings.notify_char:
            self.report_debug(
                f"selected notify characteristic char={self.bindings.notify_char_uuid}"
            )
        elif transport.flow_control is not None:
            self.report_debug("configured notify characteristic not found")

    def debug_snapshot(self) -> dict[str, Any]:
        if self._runtime_controller is None:
            return {}
        return self._runtime_controller.debug_snapshot()

    def debug_update(self, **changes: Any) -> None:
        if self._runtime_controller is None:
            if changes:
                unknown = ", ".join(sorted(changes.keys()))
                raise KeyError(f"Runtime controller does not support debug_update fields: {unknown}")
            return
        self._runtime_controller.debug_update(**changes)

    async def attach_runtime_controller(
        self,
        runtime_controller: RuntimeController | None,
        *,
        mtu_size: int,
        timeout: float,
    ):
        if runtime_controller is None:
            return None
        if runtime_controller is not self._runtime_controller:
            had_previous = self._runtime_controller is not None
            runtime_controller.adopt_previous(self._runtime_controller)
            self._runtime_controller = runtime_controller
            if not had_previous:
                await self._runtime_controller.initialize_connection(
                    self,
                    mtu_size=mtu_size,
                    timeout=timeout,
                )
                await self._runtime_controller.after_initialize(self, timeout=timeout)

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
        if self._runtime_controller is not None:
            await self._runtime_controller.initialize_connection(self, mtu_size=mtu_size, timeout=timeout)
        if not self._transport_profile.connect_packets:
            if self._runtime_controller is not None:
                await self._runtime_controller.after_initialize(self, timeout=timeout)
            return
        if not self.bindings.write_char:
            raise RuntimeError("No write characteristic available")
        response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        if self._transport_profile.connect_delay_ms > 0:
            await asyncio.sleep(self._transport_profile.connect_delay_ms / 1000.0)
        for packet in self._transport_profile.connect_packets:
            await self._write_chunks(
                client,
                self.bindings.write_char,
                packet,
                response=response,
                chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                timeout=timeout,
            )
        if self._runtime_controller is not None:
            await self._runtime_controller.after_initialize(self, timeout=timeout)

    async def send(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
        runtime_controller: RuntimeController | None = None,
    ) -> None:
        self._client = client
        if runtime_controller is not None:
            if runtime_controller is not self._runtime_controller:
                runtime_controller.adopt_previous(self._runtime_controller)
                self._runtime_controller = runtime_controller
        if not self.bindings.write_char:
            raise RuntimeError("No write characteristic available")

        bulk_write = self._transport_profile.bulk_write
        if bulk_write is not None:
            await self._send_split(
                client,
                data,
                bulk_write=bulk_write,
                mtu_size=mtu_size,
                timeout=timeout,
            )
            return
        await self._send_standard(client, data, mtu_size=mtu_size, timeout=timeout)

    async def _send_standard(
        self,
        client: Any,
        data: bytes,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        if self._runtime_controller is not None:
            self._runtime_controller.on_standard_send_started(self)
            data = self._runtime_controller.prepare_standard_payload(self, data)
            self._runtime_controller.track_outgoing_query_status(self, data)
        try:
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
            self.report_debug(
                f"write mode response={response} strategy={self.bindings.write_selection_strategy} "
                f"char={self.bindings.write_char_uuid} payload={len(data)} "
                f"mtu_payload={mtu_payload} chunk={chunk_size} chunks={chunk_count} "
                f"reserve={self._transport_profile.write_without_response_payload_reserve} "
                f"delay_ms={self._transport_profile.standard_write_delay_ms} "
                f"head={data[:16].hex()} tail={data[-16:].hex()}"
            )
            await self._write_chunks(
                client,
                self.bindings.write_char,
                data,
                response=response,
                chunk_size=chunk_size,
                delay_seconds=delay_seconds,
                timeout=timeout,
                wait_for_flow=self._transport_profile.wait_for_flow_on_standard_write,
            )
        finally:
            if self._runtime_controller is not None:
                self._runtime_controller.on_standard_send_finished(self)

    async def _send_split(
        self,
        client: Any,
        data: bytes,
        *,
        bulk_write: BleBulkWriteProfile,
        mtu_size: int,
        timeout: float,
    ) -> None:
        if not self.bindings.bulk_write_char:
            raise RuntimeError("Bulk write characteristic not found")

        split = split_prefixed_bulk_stream(
            data,
            self._protocol_family,
            bulk_write.tail_packets,
        )
        # TODO: Legacy BLE split runtime hooks for V5X/V5G/V5C live here because
        # they depend on notifications and BLE bulk/write characteristics. New
        # protocol send/query sequencing should be modeled as ProtocolJob.steps.
        split_context = None
        if self._runtime_controller is not None:
            split_context = self._runtime_controller.build_split_context(self, split)

        cmd_response = self._resolve_response_mode(
            self.bindings.write_char,
            self.bindings.write_selection_strategy,
            self.bindings.write_response_preference,
        )
        self.report_debug(
            f"split write response={cmd_response} cmd_char={self.bindings.write_char_uuid} "
            f"bulk_char={self.bindings.bulk_write_char_uuid or '<missing>'} "
            f"notify_char={self.bindings.notify_char_uuid or '<missing>'}"
        )

        for packet in split.commands:
            density_updated = False
            if self._runtime_controller is not None:
                packet, density_updated = self._runtime_controller.prepare_split_command(self, packet, split_context)
            if packet is None:
                continue
            if self._runtime_controller is not None:
                await self._runtime_controller.before_split_command(
                    self,
                    packet,
                    split_context,
                    timeout=timeout,
                    density_updated=density_updated,
                )
                ack_token = self._runtime_controller.arm_command_ack(self, packet)
            else:
                ack_token = None
            try:
                await self._write_chunks(
                    client,
                    self.bindings.write_char,
                    packet,
                    response=cmd_response,
                    chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                    delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                    timeout=timeout,
                )
                if self._runtime_controller is not None:
                    await self._runtime_controller.after_split_command(
                        self,
                        packet,
                        split_context,
                        timeout=timeout,
                        density_updated=density_updated,
                        ack_token=ack_token,
                    )
            except Exception:
                if self._runtime_controller is not None:
                    self._runtime_controller.clear_command_ack(self, ack_token)
                raise

        if split.bulk_payload:
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
                (len(split.bulk_payload) + bulk_chunk_size - 1) // bulk_chunk_size
                if bulk_chunk_size
                else 0
            )
            self.report_debug(
                f"split bulk response={bulk_response} payload={len(split.bulk_payload)} "
                f"mtu_payload={bulk_mtu_payload} chunk={bulk_chunk_size} chunks={bulk_chunk_count} "
                f"reserve={self._transport_profile.write_without_response_payload_reserve} "
                f"delay_ms={bulk_write.write_delay_ms} "
                f"flow_control={self._transport_profile.flow_control is not None}"
            )
            await self._write_chunks(
                client,
                self.bindings.bulk_write_char,
                split.bulk_payload,
                response=bulk_response,
                chunk_size=bulk_chunk_size,
                delay_seconds=bulk_write.write_delay_ms / 1000.0,
                timeout=timeout,
                wait_for_flow=self._transport_profile.flow_control is not None,
            )

        for packet in split.trailing_commands:
            await self._write_chunks(
                client,
                self.bindings.write_char,
                packet,
                response=cmd_response,
                chunk_size=min(mtu_size, self._transport_profile.standard_chunk_cap),
                delay_seconds=self._transport_profile.standard_write_delay_ms / 1000.0,
                timeout=timeout,
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
    ) -> None:
        for offset in range(0, len(data), chunk_size):
            if wait_for_flow:
                await self._wait_for_flow(timeout)
            chunk = data[offset : offset + chunk_size]
            await client.write_gatt_char(char, chunk, response=response)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)

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
        self._match_notification_waiters(payload)
        flow_control = self._transport_profile.flow_control
        if flow_control is not None:
            if payload in flow_control.pause_packets:
                self.flow_can_write = False
                self.report_debug(f"flow pause: {payload.hex()}")
                return
            if payload in flow_control.resume_packets:
                self.flow_can_write = True
                self.report_debug(f"flow resume: {payload.hex()}")
                return
        if self._runtime_controller is not None:
            self._runtime_controller.handle_notification(self, payload)
        self.report_debug(f"BLE notify: {payload.hex()}")

    def build_compat_request(self, **kwargs):
        if self._runtime_controller is None:
            return None
        return self._runtime_controller.build_compat_request(**kwargs)

    def apply_compat_result(self, **kwargs) -> None:
        if self._runtime_controller is None:
            return
        self._runtime_controller.apply_compat_result(self, **kwargs)

    def make_packet(self, opcode: int, payload: bytes) -> bytes:
        return make_packet(opcode, payload, self._protocol_family)

    def split_prefixed_packets(self, data: bytes) -> list[bytes] | None:
        packets: list[bytes] = []
        offset = 0
        while offset < len(data):
            packet_len = prefixed_packet_length(data, offset, self._protocol_family)
            if packet_len is None:
                return None
            packets.append(data[offset : offset + packet_len])
            offset += packet_len
        return packets

    def extract_prefixed_opcode(self, payload: bytes) -> Optional[int]:
        prefix = self._protocol_family.packet_prefix
        if prefix is None:
            return None
        if len(payload) < len(prefix) + 1 or payload[: len(prefix)] != prefix:
            return None
        return payload[len(prefix)]

    def extract_prefixed_payload(self, packet: bytes) -> Optional[bytes]:
        prefix = self._protocol_family.packet_prefix
        if prefix is None:
            return None
        if len(packet) < len(prefix) + 6 or packet[: len(prefix)] != prefix:
            return None
        payload_length = packet[len(prefix) + 2] | (packet[len(prefix) + 3] << 8)
        payload_start = len(prefix) + 4
        payload_end = payload_start + payload_length
        if payload_end + 2 > len(packet):
            return None
        return packet[payload_start:payload_end]

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

    def can_send_control_packet(self) -> bool:
        return bool(self._client and self.bindings.write_char)

    def can_query_control_packet(self) -> bool:
        return False

    def can_wait_for_notification(self) -> bool:
        return self.notify_started

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
        loop = asyncio.get_running_loop()
        waiter = _NotificationWaiter(
            label=label,
            match=match,
            future=loop.create_future(),
        )
        self._notification_waiters.append(waiter)
        try:
            return await asyncio.wait_for(waiter.future, timeout=max(0.0, timeout))
        except TimeoutError:
            if required:
                raise TimeoutError(f"Timed out waiting for BLE notification: {label}") from None
            self.report_debug(f"optional notification wait timed out: {label}")
            return None
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

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        _ = packet, timeout, reply_complete
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
