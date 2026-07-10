from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.helpers import build_capture_reporter
from timiniprint.devices.profiles import (
    LevelProfile,
    ModeLevelProfile,
    RuntimePreset,
    RuntimeCapabilities,
    RuntimeSettings,
)
from timiniprint.devices.bluetooth_profiles import (
    BleBulkWriteProfile,
    get_ble_transport_profile,
)
from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.printing.runtime.v5g import V5GRuntimeController
from timiniprint.printing.runtime.v5g_density import DensityLevels
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.printing.runtime.v5x_density import adjust_density_payload, start_delay_ms
from timiniprint.protocol.families import (
    split_prefixed_bulk_stream,
)
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5g import (
    V5G_CONNECT_QUERY_PACKET,
    V5G_TEMPERATURE_QUERY_PACKET,
    encode_density_payload,
)
from timiniprint.protocol.families.v5x import (
    V5X_CONNECT_INIT_PACKET,
    V5X_FINALIZE_PACKET,
    V5X_GET_SERIAL_PACKET,
    V5X_NOTIFY_GET_SERIAL_ACK,
    V5X_NOTIFY_IDLE_GET_SERIAL,
    V5X_NOTIFY_START_PRINT_OK,
    V5X_NOTIFY_START_READY,
    V5X_NOTIFY_TRIGGER_STATUS_POLL,
    V5X_STATUS_POLL_PACKET,
)
from timiniprint.protocol.families.v5c import V5C_CONNECT_INIT_PACKET
from timiniprint.protocol.packet import crc8_value, make_packet, prefixed_packet_opcode
from timiniprint.protocol.steps import ProtocolStep
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import (
    _BleWriteEndpointResolver,
)
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import (
    _BleakTransportSession,
)


class _Char:
    def __init__(self, uuid: str, properties):
        self.uuid = uuid
        self.properties = properties


class _Svc:
    def __init__(self, uuid: str, chars):
        self.uuid = uuid
        self.characteristics = chars


class _Client:
    def __init__(self, services):
        self.services = services
        self.calls = []
        self.notify_callbacks = {}
        self.stop_notify_calls = []

    async def write_gatt_char(self, char, chunk, response=True):
        self.calls.append((char.uuid, bytes(chunk), response))

    async def start_notify(self, char_uuid, callback):
        self.notify_callbacks[char_uuid] = callback

    async def stop_notify(self, char_uuid):
        self.stop_notify_calls.append(char_uuid)
        self.notify_callbacks.pop(char_uuid, None)


def _v5x_tail_packets() -> tuple[bytes, ...]:
    return (V5X_FINALIZE_PACKET,)


def _to_namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


def _controller(session):
    controller = session._runtime_controller
    assert controller is not None
    return controller


def _v5g_state(session):
    return _to_namespace(_controller(session).debug_snapshot())


def _v5x_state(session):
    return _to_namespace(_controller(session).debug_snapshot())


def _v5c_state(session):
    return _to_namespace(_controller(session).debug_snapshot())


def _make_level_profile(levels: DensityLevels) -> LevelProfile:
    return LevelProfile(low=levels.low, middle=levels.middle, high=levels.high)


def _v5g_density_packet(value: int) -> bytes:
    return make_packet(0xF2, encode_density_payload(value), ProtocolFamily.V5G)


def _make_v5g_controller(
    *,
    helper_kind: str,
    profile_runtime_preset_key: str | None,
    image_levels: DensityLevels,
    text_levels: DensityLevels,
    applies_d2_status: bool = False,
    applies_didian_status: bool = False,
) -> V5GRuntimeController:
    runtime_settings = RuntimeSettings(
        control_algorithm=helper_kind,
        preset=RuntimePreset(
            key=profile_runtime_preset_key or "test",
            control_algorithm=helper_kind,
            density=ModeLevelProfile(
                image=_make_level_profile(image_levels),
                text=_make_level_profile(text_levels),
            ),
            capabilities=RuntimeCapabilities(
                d2_status=applies_d2_status,
                didian_status=applies_didian_status,
            ),
        ),
        capabilities=RuntimeCapabilities(
            d2_status=applies_d2_status,
            didian_status=applies_didian_status,
        ),
    )
    return V5GRuntimeController(runtime_settings=runtime_settings)


def _enable_notification_waits(session: _BleakTransportSession) -> None:
    session.notify_started = True


def _runtime_controller_for_test(family: ProtocolFamily):
    if family is ProtocolFamily.V5G:
        return V5GRuntimeController()
    if family is ProtocolFamily.V5X:
        return V5XRuntimeController()
    if family is ProtocolFamily.V5C:
        return V5CRuntimeController()
    return None


def _attach_runtime_controller_for_test(
    session: _BleakTransportSession,
    family: ProtocolFamily,
) -> None:
    controller = _runtime_controller_for_test(family)
    if controller is None:
        return
    session._runtime_controller = controller


class _NotificationCountingController:
    def __init__(self) -> None:
        self.notifications: list[bytes] = []

    def adopt_previous(self, previous) -> None:
        _ = previous

    async def initialize_connection(self, session, *, mtu_size: int, timeout: float) -> None:
        _ = mtu_size, timeout
        session.handle_notification(b"during init")

    async def after_initialize(self, session, *, timeout: float) -> None:
        _ = session, timeout

    def handle_notification(self, session, payload: bytes) -> None:
        _ = session
        self.notifications.append(bytes(payload))


class _StandardRuntimeSession:
    def __init__(
        self,
        session: _BleakTransportSession,
        client: _Client,
    ) -> None:
        self._session = session
        self._client = client

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    def can_send_standard_payload(self) -> bool:
        return True

    async def send_standard_payload(self, data: bytes) -> None:
        await self._session.send(
            self._client,
            data,
            mtu_size=180,
            timeout=0.2,
        )


async def _send_v5g_runtime_payload(
    session: _BleakTransportSession,
    client: _Client,
    runtime_controller: V5GRuntimeController,
    data: bytes,
) -> None:
    await session.attach_runtime_controller(
        runtime_controller,
        mtu_size=180,
        timeout=0.2,
    )
    sent = await runtime_controller.send_protocol_steps(
        _StandardRuntimeSession(session, client),
        (ProtocolStep.send("print data", data),),
        timeout=0.2,
    )
    if not sent:
        raise AssertionError("V5G runtime did not handle standard payload steps")


class _V5XRuntimeSession:
    def __init__(
        self,
        session: _BleakTransportSession,
        client: _Client,
    ) -> None:
        self._session = session
        self._client = client
        self._session._client = client

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool:
        return await self._session.send_bulk_payload(
            self._client,
            data,
            mtu_size=180,
            timeout=timeout,
        )


async def _send_v5x_runtime_payload(
    session: _BleakTransportSession,
    client: _Client,
    data: bytes,
    *,
    timeout: float = 0.2,
) -> None:
    runtime_controller = session._runtime_controller
    if not isinstance(runtime_controller, V5XRuntimeController):
        raise AssertionError("V5X runtime controller is not attached")
    sent = await runtime_controller.send_protocol_steps(
        _V5XRuntimeSession(session, client),
        (ProtocolStep.send("print data", data),),
        timeout=timeout,
    )
    if not sent:
        raise AssertionError("V5X runtime did not handle split payload steps")


class BleakTransportSessionTests(unittest.TestCase):
    def _make_session(self, family: ProtocolFamily) -> tuple[_BleakTransportSession, _Client]:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        transport = replace(
            get_ble_transport_profile(family),
            standard_write_delay_ms=0,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        _attach_runtime_controller_for_test(session, family)
        client = _Client([])
        return session, client

    def _make_session_with_sink(self, family: ProtocolFamily):
        reporter, sink = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        transport = replace(
            get_ble_transport_profile(family),
            standard_write_delay_ms=0,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        _attach_runtime_controller_for_test(session, family)
        client = _Client([])
        return session, client, sink

    def test_configure_endpoints_prefers_profile_service_uuid(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        preferred = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        fallback = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        notify = _Char("0000ae02-0000-1000-8000-00805f9b34fb", ["notify"])
        services = [
            _Svc("11111111-0000-1000-8000-00805f9b34fb", [fallback]),
            _Svc("0000ae30-0000-1000-8000-00805f9b34fb", [preferred, notify]),
        ]

        session.configure_endpoints(services)

        self.assertIs(session.bindings.bulk_write_char, preferred)
        self.assertIs(session.bindings.notify_char, notify)

    def test_configure_endpoints_prefers_generic_notifier_for_v5g(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        notifier = _Char("12345679-0000-1000-8000-00805f9b34fb", ["notify"])
        services = [
            _Svc(
                "00001800-0000-1000-8000-00805f9b34fb",
                [_Char("00002a00-0000-1000-8000-00805f9b34fb", ["read"])],
            ),
            _Svc("12345678-0000-1000-8000-00805f9b34fb", [notifier]),
        ]

        session.configure_endpoints(services)

        self.assertIs(session.bindings.notify_char, notifier)
        self.assertEqual(
            session.bindings.notify_char_uuid,
            "12345679-0000-1000-8000-00805f9b34fb",
        )

    def test_start_and_stop_notify_use_bound_notify_characteristic(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        notify = _Char("0000ae02-0000-1000-8000-00805f9b34fb", ["notify"])
        session.bindings.notify_char = notify
        session.bindings.notify_char_uuid = notify.uuid

        async def run() -> None:
            await session.start_notify_if_available(client, lambda *_args: None)
            await session.stop_notify_if_started(client)

        asyncio.run(run())

        self.assertEqual(list(client.notify_callbacks.keys()), [])
        self.assertEqual(client.stop_notify_calls, [notify.uuid])
        self.assertFalse(session.notify_started)

    def test_notification_wait_matches_expected_payload(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)
        expected = make_packet(0xD3, bytes([60]), ProtocolFamily.V5G)

        async def run() -> None:
            task = asyncio.create_task(
                session.wait_for_notification(
                    "temperature",
                    lambda payload: payload == expected,
                    timeout=0.2,
                )
            )
            await asyncio.sleep(0)
            session.handle_notification(expected)
            self.assertEqual(await task, expected)

        asyncio.run(run())

    def test_notification_wait_matches_recent_payload(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)
        expected = make_packet(0xD3, bytes([60]), ProtocolFamily.V5G)
        session.handle_notification(expected)

        async def run() -> None:
            reply = await session.wait_for_notification(
                "temperature",
                lambda payload: payload == expected,
                timeout=0.01,
            )
            self.assertEqual(reply, expected)

        asyncio.run(run())

    def test_notification_query_does_not_match_recent_payload(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)
        expected = make_packet(0xD3, bytes([60]), ProtocolFamily.V5G)
        session.handle_notification(expected)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session._client = client
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                reply = await session.send_control_packet_wait_notification(
                    b"\x01",
                    label="fresh temperature",
                    match=lambda payload: payload == expected,
                    timeout=0.01,
                    required=False,
                )
                self.assertIsNone(reply)
                write_chunks.assert_awaited_once()

        asyncio.run(run())

    def test_notification_wait_keeps_unmatched_payload_for_runtime_state(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)

        async def run() -> None:
            task = asyncio.create_task(
                session.wait_for_notification(
                    "temperature",
                    lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5G) == 0xD3,
                    timeout=0.2,
                )
            )
            await asyncio.sleep(0)
            session.handle_notification(make_packet(0xA3, bytes([0x08]), ProtocolFamily.V5G))
            self.assertFalse(task.done())
            self.assertTrue(_v5g_state(session).didian_status)
            temperature = make_packet(0xD3, bytes([60]), ProtocolFamily.V5G)
            session.handle_notification(temperature)
            self.assertEqual(await task, temperature)

        asyncio.run(run())

    def test_required_notification_wait_timeout_raises(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)

        async def run() -> None:
            with self.assertRaisesRegex(TimeoutError, "temperature"):
                await session.wait_for_notification(
                    "temperature",
                    lambda _payload: False,
                    timeout=0.01,
                )

        asyncio.run(run())

    def test_optional_notification_wait_timeout_returns_none(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)
        _enable_notification_waits(session)

        async def run() -> None:
            reply = await session.wait_for_notification(
                "optional temperature",
                lambda _payload: False,
                timeout=0.01,
                required=False,
            )
            self.assertIsNone(reply)

        asyncio.run(run())

    def test_multiple_notification_waiters_do_not_consume_each_other(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        _enable_notification_waits(session)

        async def run() -> None:
            wait_a7 = asyncio.create_task(
                session.wait_for_notification(
                    "a7",
                    lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5X) == 0xA7,
                    timeout=0.2,
                )
            )
            wait_aa = asyncio.create_task(
                session.wait_for_notification(
                    "aa",
                    lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5X) == 0xAA,
                    timeout=0.2,
                )
            )
            await asyncio.sleep(0)
            session.handle_notification(V5X_NOTIFY_GET_SERIAL_ACK)
            self.assertEqual(
                await asyncio.wait_for(wait_a7, timeout=0.2),
                V5X_NOTIFY_GET_SERIAL_ACK,
            )
            self.assertFalse(wait_aa.done())
            session.handle_notification(V5X_NOTIFY_START_READY)
            self.assertEqual(await wait_aa, V5X_NOTIFY_START_READY)

        asyncio.run(run())

    def test_initialize_connection_sends_family_init_packets(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.initialize_connection(
                client,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5X_CONNECT_INIT_PACKET, False)])

    def test_v5x_transport_uses_source_like_bulk_pacing(self) -> None:
        transport = get_ble_transport_profile(ProtocolFamily.V5X)
        bulk_write = transport.bulk_write

        self.assertIsInstance(bulk_write, BleBulkWriteProfile)
        self.assertEqual(bulk_write.chunk_cap, 180)
        self.assertEqual(bulk_write.write_delay_ms, 30)
        self.assertEqual(transport.write_without_response_payload_reserve, 5)

    def test_v5x_missing_optional_connect_info_does_not_fail(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        transport = replace(
            get_ble_transport_profile(ProtocolFamily.V5X),
            standard_write_delay_ms=0,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        _attach_runtime_controller_for_test(session, ProtocolFamily.V5X)
        client = _Client([])
        _enable_notification_waits(session)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.initialize_connection(
                client,
                mtu_size=180,
                timeout=0.01,
            )

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5X_CONNECT_INIT_PACKET, False)])
        self.assertFalse(_v5x_state(session).connect_info_received)
        self.assertFalse(_v5x_state(session).await_connect_info)

    def test_attach_runtime_controller_replays_early_notifications(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        transport = replace(
            get_ble_transport_profile(ProtocolFamily.V5X),
            standard_write_delay_ms=0,
        )
        session = _BleakTransportSession(
            transport,
            resolver,
            reporter,
        )
        client = _Client([])
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session._client = client
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _enable_notification_waits(session)
        session.handle_notification(
            make_packet(0xB1, b"FW1.0.22", ProtocolFamily.V5X)
        )
        controller = V5XRuntimeController()

        async def run() -> None:
            with patch("timiniprint.printing.runtime.v5x.asyncio.sleep", new=AsyncMock()):
                await session.attach_runtime_controller(
                    controller,
                    mtu_size=180,
                    timeout=0.01,
                )

        asyncio.run(run())

        self.assertTrue(controller.debug_snapshot()["connect_info_received"])
        self.assertFalse(controller.debug_snapshot()["await_connect_info"])

    def test_attach_runtime_controller_processes_init_notifications_once(self) -> None:
        reporter, _ = build_capture_reporter()
        session = _BleakTransportSession(
            get_ble_transport_profile(ProtocolFamily.V5X),
            _BleWriteEndpointResolver(reporter=reporter),
            reporter,
        )
        session.handle_notification(b"before attach")
        controller = _NotificationCountingController()

        async def run() -> None:
            await session.attach_runtime_controller(
                controller,
                mtu_size=180,
                timeout=0.01,
            )

        asyncio.run(run())

        self.assertEqual(
            controller.notifications,
            [b"before attach", b"during init"],
        )

    def test_initialize_connection_waits_for_family_settle_delay(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        async def run() -> None:
            with patch(
                "timiniprint.printing.runtime.v5x.asyncio.sleep",
                new=fake_sleep,
            ), patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await session.initialize_connection(
                    client,
                    mtu_size=180,
                    timeout=0.2,
                )
                write_chunks.assert_awaited_once()

        asyncio.run(run())

        self.assertIn(0.2, sleep_calls)

    def test_initialize_connection_sends_v5c_init_packet(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5C)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.initialize_connection(
                client,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5C_CONNECT_INIT_PACKET, False)])

    def test_initialize_connection_sends_v5g_query_packet(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.initialize_connection(
                client,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5G_CONNECT_QUERY_PACKET, False)])

    def test_initialize_connection_waits_for_v5c_settle_delay(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5C)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        async def run() -> None:
            with patch(
                "timiniprint.printing.runtime.v5c.asyncio.sleep",
                new=fake_sleep,
            ), patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await session.initialize_connection(
                    client,
                    mtu_size=180,
                    timeout=0.2,
                )
                write_chunks.assert_awaited_once()

        asyncio.run(run())

        self.assertIn(0.6, sleep_calls)

    def test_send_standard_uses_transport_profile_chunk_cap(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        transport = replace(
            get_ble_transport_profile(ProtocolFamily.V5C),
            standard_chunk_cap=7,
            standard_write_delay_ms=0,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        client = _Client([])
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.send(
                client,
                b"X" * 20,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual([len(call[1]) for call in client.calls], [7, 7, 6])

    def test_v5g_transport_uses_multi_row_ble_pacing(self) -> None:
        transport = get_ble_transport_profile(ProtocolFamily.V5G)

        self.assertEqual(transport.standard_chunk_cap, 56 * 8)
        self.assertEqual(transport.standard_write_delay_ms, 30)
        self.assertEqual(transport.write_without_response_payload_reserve, 5)

    def test_v5g_standard_send_uses_negotiated_mtu_over_twenty_byte_fallback(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        cmd.max_write_without_response_size = 244
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.send(
                client,
                b"X" * 500,
                mtu_size=20,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual([len(call[1]) for call in client.calls], [239, 239, 22])

    def test_v5g_standard_send_keeps_twenty_byte_fallback_without_reported_mtu(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid

        async def run() -> None:
            await session.send(
                client,
                b"X" * 45,
                mtu_size=20,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual([len(call[1]) for call in client.calls], [15, 15, 15])

    def test_v5g_notifications_update_session_state(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5G)

        session.handle_notification(make_packet(0xA3, bytes([0x08]), ProtocolFamily.V5G))
        session.handle_notification(make_packet(0xD3, bytes([60]), ProtocolFamily.V5G))
        session.handle_notification(make_packet(0xD2, bytes([0x01]), ProtocolFamily.V5G))
        session.handle_notification(make_packet(0xA3, bytes([0x00]), ProtocolFamily.V5G))

        self.assertFalse(_v5g_state(session).didian_status)
        self.assertTrue(_v5g_state(session).d2_status)
        self.assertEqual(_v5g_state(session).temperature_c, 60)

    def test_v5g_probe_queries_temperature_before_job_build(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session._client = client
        _enable_notification_waits(session)
        runtime_controller = _make_v5g_controller(
            helper_kind="mx10",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
        )

        async def run() -> None:
            await session.attach_runtime_controller(
                runtime_controller,
                mtu_size=180,
                timeout=0.2,
            )

            async def notify_temperature() -> None:
                while not client.calls:
                    await asyncio.sleep(0)
                session.handle_notification(make_packet(0xD3, bytes([28]), ProtocolFamily.V5G))

            task = asyncio.create_task(notify_temperature())
            await runtime_controller.probe_capabilities(session, timeout=0.2)
            await task

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5G_TEMPERATURE_QUERY_PACKET, False)])
        self.assertEqual(runtime_controller.debug_snapshot()["temperature_c"], 28)

    def test_v5g_mx10_helper_rewrites_single_density_packet(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(temperature_c=60)
        runtime_controller = _make_v5g_controller(
            helper_kind="mx10",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + _v5g_density_packet(180)
            + make_packet(0xA2, b"\x55" * 40, ProtocolFamily.V5G)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                self.assertIn(
                    _v5g_density_packet(120),
                    sent,
                )

        asyncio.run(run())

    def test_v5g_helper_selects_text_density_from_f2_value_when_be_is_image_mode(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(temperature_c=54)
        runtime_controller = _make_v5g_controller(
            helper_kind="mx10",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + _v5g_density_packet(130)
            + make_packet(0xA2, b"\x55" * 40, ProtocolFamily.V5G)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                self.assertIn(
                    _v5g_density_packet(110),
                    sent,
                )

        asyncio.run(run())

    def test_v5g_mx10_helper_rewrites_continuous_density_packets(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(temperature_c=50)
        runtime_controller = _make_v5g_controller(
            helper_kind="mx10",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        density_packet = _v5g_density_packet(180)
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + (density_packet * 5)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                expected_values = [160, 145, 130, 115, 100]
                for value in expected_values:
                    self.assertIn(
                        _v5g_density_packet(value),
                        sent,
                    )

        asyncio.run(run())

    def test_v5g_pd01_helper_uses_pd01_curve(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(temperature_c=60)
        runtime_controller = _make_v5g_controller(
            helper_kind="pd01",
            profile_runtime_preset_key="mx11",
            image_levels=DensityLevels(low=100, middle=130, high=150),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=False,
            applies_didian_status=False,
        )
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + _v5g_density_packet(130)
            + make_packet(0xA2, b"\x55" * 40, ProtocolFamily.V5G)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                self.assertIn(
                    _v5g_density_packet(100),
                    sent,
                )

        asyncio.run(run())

    def test_v5g_mx06_helper_rewrites_single_density_packet_from_last_value(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(
            d2_status=True,
            last_complete_time=time.time(),
            last_single_density_value=150,
        )
        runtime_controller = _make_v5g_controller(
            helper_kind="mx06",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + _v5g_density_packet(180)
            + make_packet(0xA2, b"\x55" * 40, ProtocolFamily.V5G)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                self.assertIn(
                    _v5g_density_packet(130),
                    sent,
                )

        asyncio.run(run())

    def test_v5g_mx06_helper_rewrites_continuous_density_packets(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        _controller(session).debug_update(
            d2_status=True,
            last_complete_time=time.time(),
            last_print_record_density=150,
        )
        runtime_controller = _make_v5g_controller(
            helper_kind="mx06",
            profile_runtime_preset_key="mx06",
            image_levels=DensityLevels(low=150, middle=180, high=200),
            text_levels=DensityLevels(low=100, middle=130, high=150),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        density_packet = _v5g_density_packet(180)
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + (density_packet * 6)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                expected_values = [140, 140, 140, 140, 135, 130]
                for value in expected_values:
                    self.assertIn(
                        _v5g_density_packet(value),
                        sent,
                    )

        asyncio.run(run())

    def test_v5g_d2_helper_rewrites_generic_continuous_branch(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        runtime_controller = _make_v5g_controller(
            helper_kind="d2",
            profile_runtime_preset_key="mx08",
            image_levels=DensityLevels(low=60, middle=90, high=110),
            text_levels=DensityLevels(low=50, middle=70, high=100),
            applies_d2_status=True,
            applies_didian_status=False,
        )
        density_packet = _v5g_density_packet(110)
        data = (
            make_packet(0xA4, bytes([0x33]), ProtocolFamily.V5G)
            + make_packet(0xBE, bytes([0x00]), ProtocolFamily.V5G)
            + (density_packet * 5)
        )

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                await _send_v5g_runtime_payload(
                    session,
                    client,
                    runtime_controller,
                    data,
                )
                sent = write_chunks.await_args.args[2]
                expected_values = [90, 90, 90, 90, 80]
                for value in expected_values:
                    self.assertIn(
                        _v5g_density_packet(value),
                        sent,
                    )

        asyncio.run(run())

    def test_v5g_pd01_temperature_drop_resets_density_when_idle(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5G)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session._client = client
        _controller(session).debug_update(helper_kind="pd01", temperature_c=80)

        async def run() -> None:
            with patch.object(session, "_write_chunks", new=AsyncMock()) as write_chunks:
                session.handle_notification(make_packet(0xD3, bytes([60]), ProtocolFamily.V5G))
                await asyncio.sleep(0)
                write_chunks.assert_awaited_once()
                packet = write_chunks.await_args.args[2]
                self.assertEqual(
                    packet,
                    _v5g_density_packet(120),
                )

        asyncio.run(run())

    def test_v5x_runtime_routes_commands_bulk_and_trailing_packets(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        _enable_notification_waits(session)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.bulk_write_char = bulk
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session.bindings.bulk_write_char_uuid = bulk.uuid

        data = (
            V5X_GET_SERIAL_PACKET
            + bytes.fromhex("2221A20001005D94FF")
            + bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 16)
            + V5X_FINALIZE_PACKET
        )

        async def run() -> None:
            async def notify() -> None:
                while len(client.calls) < 1:
                    await asyncio.sleep(0.001)
                session.handle_notification(V5X_NOTIFY_GET_SERIAL_ACK)
                session.handle_notification(V5X_NOTIFY_START_READY)
                while len(client.calls) < 3:
                    await asyncio.sleep(0.001)
                session.handle_notification(V5X_NOTIFY_START_PRINT_OK)

            task = asyncio.create_task(notify())
            await _send_v5x_runtime_payload(session, client, data)
            await task

        asyncio.run(run())

        self.assertEqual(client.calls[0][0], cmd.uuid)
        self.assertEqual(client.calls[1][0], cmd.uuid)
        self.assertEqual(client.calls[2][0], cmd.uuid)
        self.assertEqual(client.calls[3][0], bulk.uuid)
        self.assertEqual(client.calls[4][0], cmd.uuid)

    def test_bulk_send_uses_chunk_cap_from_transport_profile(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        base_transport = get_ble_transport_profile(ProtocolFamily.V5X)
        self.assertIsNotNone(base_transport.bulk_write)
        transport = replace(
            base_transport,
            standard_write_delay_ms=0,
            bulk_write=replace(base_transport.bulk_write, write_delay_ms=0, chunk_cap=5),
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        client = _Client([])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.bulk_write_char = bulk
        session.bindings.bulk_write_char_uuid = bulk.uuid
        session._client = client

        async def run() -> None:
            await session.send_bulk_payload(
                client,
                b"\xAA" * 12,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertTrue(all(call[0] == bulk.uuid for call in client.calls))
        self.assertEqual([len(call[1]) for call in client.calls], [5, 5, 2])

    def test_bulk_send_applies_write_without_response_payload_reserve(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        base_transport = get_ble_transport_profile(ProtocolFamily.V5X)
        self.assertIsNotNone(base_transport.bulk_write)
        transport = replace(
            base_transport,
            standard_write_delay_ms=0,
            bulk_write=replace(base_transport.bulk_write, write_delay_ms=0, chunk_cap=180),
            write_without_response_payload_reserve=5,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        client = _Client([])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk.max_write_without_response_size = 13
        session.bindings.bulk_write_char = bulk
        session.bindings.bulk_write_char_uuid = bulk.uuid
        session._client = client

        async def run() -> None:
            await session.send_bulk_payload(
                client,
                b"\xAA" * 12,
                mtu_size=180,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual([len(call[1]) for call in client.calls], [8, 4])

    def test_bulk_send_keeps_twenty_byte_fallback_without_reported_mtu(self) -> None:
        reporter, _ = build_capture_reporter()
        resolver = _BleWriteEndpointResolver(reporter=reporter)
        base_transport = get_ble_transport_profile(ProtocolFamily.V5X)
        self.assertIsNotNone(base_transport.bulk_write)
        transport = replace(
            base_transport,
            standard_write_delay_ms=0,
            bulk_write=replace(base_transport.bulk_write, write_delay_ms=0, chunk_cap=180),
            write_without_response_payload_reserve=5,
        )
        session = _BleakTransportSession(transport, resolver, reporter)
        client = _Client([])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.bulk_write_char = bulk
        session.bindings.bulk_write_char_uuid = bulk.uuid
        session._client = client

        async def run() -> None:
            await session.send_bulk_payload(
                client,
                b"\xAA" * 45,
                mtu_size=20,
                timeout=0.2,
            )

        asyncio.run(run())

        self.assertEqual([len(call[1]) for call in client.calls], [15, 15, 15])

    def test_v5x_skips_redundant_density_command_on_same_connection(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        _enable_notification_waits(session)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.bulk_write_char = bulk
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session.bindings.bulk_write_char_uuid = bulk.uuid

        data = (
            V5X_GET_SERIAL_PACKET
            + bytes.fromhex("2221A20001005D94FF")
            + bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 8)
            + V5X_FINALIZE_PACKET
        )

        async def run_once() -> None:
            async def notify() -> None:
                while len(client.calls) < 1:
                    await asyncio.sleep(0.001)
                session.handle_notification(V5X_NOTIFY_GET_SERIAL_ACK)
                session.handle_notification(V5X_NOTIFY_START_READY)
                while True:
                    if client.calls and client.calls[-1][1].startswith(bytes.fromhex("2221A900")):
                        break
                    await asyncio.sleep(0.001)
                session.handle_notification(V5X_NOTIFY_START_PRINT_OK)

            task = asyncio.create_task(notify())
            await _send_v5x_runtime_payload(session, client, data)
            await task

        asyncio.run(run_once())
        first_job_calls = list(client.calls)
        client.calls.clear()
        asyncio.run(run_once())

        self.assertEqual(
            [call[1] for call in first_job_calls[:3]],
            [
                V5X_GET_SERIAL_PACKET,
                bytes.fromhex("2221A20001005D94FF"),
                bytes.fromhex("2221A9000600010030010000EBFF"),
            ],
        )
        self.assertEqual(
            [call[1] for call in client.calls[:2]],
            [
                V5X_GET_SERIAL_PACKET,
                bytes.fromhex("2221A9000600010030010000EBFF"),
            ],
        )

    def test_v5x_notifications_update_session_state(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("112233445566"), ProtocolFamily.V5X)
        )
        session.handle_notification(make_packet(0xB0, bytes([0x01]), ProtocolFamily.V5X))
        session.handle_notification(
            make_packet(0xB1, b"FW1.0.22", ProtocolFamily.V5X)
        )
        session.handle_notification(
            make_packet(0xA1, bytes([0x01, 0x00, 0x00, 0x63, 0x1E, 0x00, 0x00, 0x00]), ProtocolFamily.V5X)
        )
        session.handle_notification(make_packet(0xA9, bytes([0x00]), ProtocolFamily.V5X))

        self.assertEqual(_v5x_state(session).device_serial, "112233445566")
        self.assertTrue(_v5x_state(session).serial_valid)
        self.assertEqual(_v5x_state(session).last_a7_payload, bytes.fromhex("112233445566"))
        self.assertEqual(_v5x_state(session).print_head_type, "gaoya")
        self.assertEqual(_v5x_state(session).firmware_version, "FW1.0.22")
        self.assertTrue(_v5x_state(session).connect_info_received)
        self.assertEqual(_v5x_state(session).last_a9_status, 0x00)
        self.assertEqual(_v5x_state(session).task_state, 0x01)
        self.assertEqual(_v5x_state(session).task_state_name, "printing")
        self.assertEqual(_v5x_state(session).battery_level, 99)
        self.assertEqual(_v5x_state(session).temperature_c, 30)
        self.assertEqual(_v5x_state(session).error_group, 0x00)
        self.assertEqual(_v5x_state(session).error_code, 0x00)
        self.assertEqual(_v5x_state(session).compatibility.mode, "auth")
        debug_details = [
            message.detail or message.short
            for message in sink.messages
            if message.level == "debug"
        ]
        self.assertIn(
            "V5X firmware: version=FW1.0.22, print_head_type=gaoya",
            debug_details,
        )

    def test_v5x_framed_a9_status_uses_payload_byte_not_crc(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(make_packet(0xA9, bytes([0x03]), ProtocolFamily.V5X))

        self.assertEqual(_v5x_state(session).last_a9_status, 0x03)

    def test_v5x_invalid_serial_is_marked_invalid(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("FFFFFFFFFFFF"), ProtocolFamily.V5X)
        )

        self.assertEqual(_v5x_state(session).device_serial, "ffffffffffff")
        self.assertFalse(_v5x_state(session).serial_valid)
        self.assertEqual(_v5x_state(session).compatibility.mode, "get_sn")

    def test_v5x_builds_compat_request_for_auth_mode(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("112233445566"), ProtocolFamily.V5X)
        )

        request = _controller(session).build_compat_request(
            ble_name="JK01",
            ble_address="48:0F:57:49:1D:3A",
        )

        self.assertEqual(
            request,
            {
                "mode": "auth",
                "ble_name": "JK01",
                "ble_address": "48:0F:57:49:1D:3A",
                "ble_sn": "112233445566",
                "ble_model": "V5X",
            },
        )

    def test_v5x_builds_compat_request_for_get_sn_mode(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("FFFFFFFFFFFF"), ProtocolFamily.V5X)
        )

        request = _controller(session).build_compat_request(
            ble_name="JK01",
            ble_address="48:0F:57:49:1D:3A",
            ble_model="JK01",
        )

        self.assertEqual(
            request,
            {
                "mode": "get_sn",
                "ble_name": "JK01",
                "ble_address": "48:0F:57:49:1D:3A",
                "ble_sn": "ffffffffffff",
                "ble_model": "JK01",
            },
        )

    def test_v5x_compat_result_is_non_blocking_and_warns_on_rejection(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("112233445566"), ProtocolFamily.V5X)
        )
        _controller(session).apply_compat_result(session, mode="auth", result_code=-2)

        self.assertTrue(_v5x_state(session).compatibility.checked)
        self.assertFalse(_v5x_state(session).compatibility.confirmed)
        self.assertEqual(_v5x_state(session).compatibility.last_result_code, -2)
        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5X compatibility check failed")

    def test_v5x_compat_result_keeps_backend_write_cmd(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(
            make_packet(0xA7, bytes.fromhex("FFFFFFFFFFFF"), ProtocolFamily.V5X)
        )
        _controller(session).apply_compat_result(
            session,
            mode="get_sn",
            result_code=1,
            write_cmd=bytes.fromhex("2221A70000000000"),
        )

        self.assertTrue(_v5x_state(session).compatibility.checked)
        self.assertTrue(_v5x_state(session).compatibility.confirmed)
        self.assertEqual(_v5x_state(session).compatibility.last_result_code, 1)
        self.assertEqual(
            _v5x_state(session).compatibility.backend_write_cmd,
            bytes.fromhex("2221A70000000000"),
        )

    def test_v5x_status_poll_ack_is_tracked(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(make_packet(0xA3, bytes([0x00]), ProtocolFamily.V5X))

        self.assertTrue(_v5x_state(session).status_poll_ack_seen)

    def test_v5x_ab_status_is_tracked(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)

        session.handle_notification(make_packet(0xAB, bytes([0x11]), ProtocolFamily.V5X))

        self.assertEqual(_v5x_state(session).last_ab_status, 0x11)

    def test_v5x_error_status_warns_once_per_signature(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5X)

        error_packet = make_packet(
            0xA1,
            bytes([0x01, 0x00, 0x00, 0x50, 0x1E, 0x00, 0x02, 0x09]),
            ProtocolFamily.V5X,
        )
        session.handle_notification(error_packet)
        session.handle_notification(error_packet)

        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5X printer reported an error status")
        self.assertIn("Task=printing", warnings[0].detail)
        self.assertIn("error_group=0x02", warnings[0].detail)
        self.assertIn("error_code=0x09", warnings[0].detail)

    def test_v5x_b3_warns_without_blocking_session(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5X)

        session.handle_notification(make_packet(0xB3, bytes([0x01]), ProtocolFamily.V5X))
        session.handle_notification(make_packet(0xB3, bytes([0x01]), ProtocolFamily.V5X))

        self.assertTrue(_v5x_state(session).mxw_sign_requested)
        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5X printer requested an additional signing step")

    def test_v5x_density_is_adjusted_using_session_state_and_coverage(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        session.handle_notification(make_packet(0xB0, bytes([0x01]), ProtocolFamily.V5X))
        session.handle_notification(
            make_packet(
                0xA1,
                bytes([0x01, 0x00, 0x00, 0x63, 0x41, 0x00, 0x00, 0x00]),
                ProtocolFamily.V5X,
            )
        )

        split = split_prefixed_bulk_stream(
            bytes.fromhex("2221A20001005D94FF")
            + bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 16)
            + V5X_FINALIZE_PACKET,
            ProtocolFamily.V5X,
            _v5x_tail_packets(),
        )
        context = session._runtime_controller._build_job_context(session, split)
        self.assertIsNotNone(context)

        state = session._runtime_controller.debug_snapshot()
        adjusted = adjust_density_payload(
            bytes([0x5D]),
            context,
            temperature_c=state["temperature_c"] or 0,
            head_type=state["print_head_type"],
        )

        self.assertEqual(adjusted, bytes([0x05]))

    def test_v5x_start_delay_prefers_gaoya_high_coverage_rule(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        _controller(session).debug_update(print_head_type="gaoya")
        split = split_prefixed_bulk_stream(
            bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 16)
            + V5X_FINALIZE_PACKET,
            ProtocolFamily.V5X,
            _v5x_tail_packets(),
        )
        context = session._runtime_controller._build_job_context(session, split)
        self.assertIsNotNone(context)

        delay_ms = start_delay_ms(
            context,
            density_updated=True,
            head_type="gaoya",
        )

        self.assertEqual(delay_ms, 200)

    def test_v5x_start_delay_uses_short_density_settle_for_lower_coverage(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        _controller(session).debug_update(print_head_type="diya")
        split = split_prefixed_bulk_stream(
            bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\x80" * 8)
            + V5X_FINALIZE_PACKET,
            ProtocolFamily.V5X,
            _v5x_tail_packets(),
        )
        context = session._runtime_controller._build_job_context(session, split)
        self.assertIsNotNone(context)

        delay_ms = start_delay_ms(
            context,
            density_updated=True,
            head_type="diya",
        )

        self.assertEqual(delay_ms, 60)

    def test_v5x_gray_job_context_recognizes_len2_start_packet(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5X)
        height_bytes = bytes([0x01, 0x00])
        split = split_prefixed_bulk_stream(
            (bytes.fromhex("2221A9000200") + height_bytes + bytes([crc8_value(height_bytes), 0xFF]))
            + bytes.fromhex("FEDCBA98")
            + V5X_FINALIZE_PACKET,
            ProtocolFamily.V5X,
            _v5x_tail_packets(),
        )

        context = session._runtime_controller._build_job_context(session, split)

        self.assertIsNotNone(context)
        self.assertTrue(context.is_gray)
        self.assertEqual(context.coverage_ratio, 0.0)

    def test_v5x_b2_notification_schedules_status_poll(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session._client = client

        async def run() -> None:
            session.handle_notification(V5X_NOTIFY_TRIGGER_STATUS_POLL)
            await asyncio.sleep(0.75)

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5X_STATUS_POLL_PACKET, False)])

    def test_v5x_a6_notification_requests_serial_when_idle(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session._client = client

        async def run() -> None:
            session.handle_notification(V5X_NOTIFY_IDLE_GET_SERIAL)
            await asyncio.sleep(0.05)

        asyncio.run(run())

        self.assertEqual(client.calls, [(cmd.uuid, V5X_GET_SERIAL_PACKET, False)])

    def test_v5x_timeout_clears_pending_handshake_state(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        _enable_notification_waits(session)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.bulk_write_char = bulk
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session.bindings.bulk_write_char_uuid = bulk.uuid

        data = V5X_GET_SERIAL_PACKET + (b"\xAA\x55" * 8) + V5X_FINALIZE_PACKET

        async def run() -> None:
            with self.assertRaises(TimeoutError):
                await _send_v5x_runtime_payload(
                    session,
                    client,
                    data,
                    timeout=0.01,
                )

        asyncio.run(run())

        self.assertEqual(_v5x_state(session).pending_command_ack_opcodes, [])
        self.assertFalse(_v5x_state(session).await_start_ready)

    def test_v5x_nonzero_a9_status_fails_immediately(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5X)
        _enable_notification_waits(session)
        cmd = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        bulk = _Char("0000ae03-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = cmd
        session.bindings.bulk_write_char = bulk
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = cmd.uuid
        session.bindings.bulk_write_char_uuid = bulk.uuid

        data = (
            V5X_GET_SERIAL_PACKET
            + bytes.fromhex("2221A9000600010030010000EBFF")
            + (b"\xAA\x55" * 8)
            + V5X_FINALIZE_PACKET
        )

        async def run() -> None:
            async def notify() -> None:
                while len(client.calls) < 1:
                    await asyncio.sleep(0.001)
                session.handle_notification(V5X_NOTIFY_GET_SERIAL_ACK)
                session.handle_notification(V5X_NOTIFY_START_READY)
                while len(client.calls) < 2:
                    await asyncio.sleep(0.001)
                session.handle_notification(make_packet(0xA9, bytes([0x03]), ProtocolFamily.V5X))

            task = asyncio.create_task(notify())
            with self.assertRaisesRegex(RuntimeError, "status=0x03"):
                await _send_v5x_runtime_payload(session, client, data)
            await task

        asyncio.run(run())

    def test_flow_controlled_standard_send_waits_for_resume(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5C)
        write_char = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = write_char
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = write_char.uuid
        session.flow_can_write = False

        async def run() -> None:
            async def resume() -> None:
                await asyncio.sleep(0.02)
                session.handle_notification(bytes.fromhex("5688A70101000000FF"))

            task = asyncio.create_task(resume())
            await session.send(
                client,
                b"ABC",
                mtu_size=180,
                timeout=0.2,
            )
            await task

        asyncio.run(run())

        self.assertEqual(client.calls, [(write_char.uuid, b"ABC", False)])
        self.assertTrue(session.flow_can_write)

    def test_flow_controlled_standard_send_times_out_without_resume(self) -> None:
        session, client = self._make_session(ProtocolFamily.V5C)
        write_char = _Char("0000ae01-0000-1000-8000-00805f9b34fb", ["write-without-response"])
        session.bindings.write_char = write_char
        session.bindings.write_selection_strategy = "preferred_uuid"
        session.bindings.write_response_preference = False
        session.bindings.write_char_uuid = write_char.uuid
        session.flow_can_write = False

        async def run() -> None:
            with self.assertRaisesRegex(TimeoutError, "flow-control resume"):
                await session.send(
                    client,
                    b"ABC",
                    mtu_size=180,
                    timeout=0.01,
                )

        asyncio.run(run())

        self.assertEqual(client.calls, [])
        self.assertFalse(session.flow_can_write)

    def test_v5c_notifications_update_session_state(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        session.handle_notification(make_packet(0xA1, bytes([0x80]), ProtocolFamily.V5C))
        session.handle_notification(make_packet(0xAA, (800).to_bytes(2, "little"), ProtocolFamily.V5C))
        session.handle_notification(
            make_packet(0xA9, bytes.fromhex("1122334455667788"), ProtocolFamily.V5C)
        )

        self.assertEqual(_v5c_state(session).status_code, 0x80)
        self.assertEqual(_v5c_state(session).status_name, "printing")
        self.assertFalse(_v5c_state(session).is_charging)
        self.assertFalse(_v5c_state(session).print_complete_seen)
        self.assertEqual(_v5c_state(session).max_print_height, 800)
        self.assertEqual(_v5c_state(session).device_serial, "1122334455667788")
        self.assertTrue(_v5c_state(session).serial_valid)
        self.assertEqual(
            _v5c_state(session).last_auth_payload,
            bytes.fromhex("1122334455667788"),
        )
        self.assertEqual(_v5c_state(session).compatibility.mode, "auth")
        self.assertEqual(_v5c_state(session).compatibility.last_trigger_opcode, 0xA9)

    def test_v5c_invalid_serial_switches_to_get_sn_mode(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        session.handle_notification(
            make_packet(0xA9, bytes.fromhex("0000000000000000"), ProtocolFamily.V5C)
        )

        self.assertEqual(_v5c_state(session).device_serial, "0000000000000000")
        self.assertFalse(_v5c_state(session).serial_valid)
        self.assertEqual(_v5c_state(session).compatibility.mode, "get_sn")
        self.assertEqual(_v5c_state(session).compatibility.last_trigger_opcode, 0xA9)

    def test_v5c_builds_compat_request(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        self.assertIsNone(
            _controller(session).build_compat_request(
                ble_name="YTB01",
                ble_address="48:0F:57:49:1D:3A",
            )
        )

        session.handle_notification(
            make_packet(0xA9, bytes.fromhex("1122334455667788"), ProtocolFamily.V5C)
        )

        request = _controller(session).build_compat_request(
            ble_name="YTB01",
            ble_address="48:0F:57:49:1D:3A",
        )

        self.assertEqual(
            request,
            {
                "mode": "auth",
                "ble_name": "YTB01",
                "ble_address": "48:0F:57:49:1D:3A",
                "ble_sn": "1122334455667788",
                "ble_model": "V5C",
            },
        )
        self.assertTrue(_v5c_state(session).compatibility.request_pending)

    def test_v5c_a8_builds_to_auth_request_from_full_packet(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        packet = make_packet(0xA8, bytes.fromhex("1122334455667788"), ProtocolFamily.V5C)
        session.handle_notification(packet)

        request = _controller(session).build_compat_request(
            ble_name="YTB01",
            ble_address="48:0F:57:49:1D:3A",
        )

        self.assertEqual(
            request,
            {
                "mode": "to_auth",
                "ble_name": "YTB01",
                "ble_address": "48:0F:57:49:1D:3A",
                "ble_sn": packet.hex(),
                "ble_model": "V5C",
            },
        )
        self.assertEqual(_v5c_state(session).compatibility.last_trigger_opcode, 0xA8)
        self.assertIsNone(_v5c_state(session).serial_valid)
        self.assertTrue(_v5c_state(session).compatibility.request_pending)

    def test_v5c_compat_result_is_non_blocking_and_warns_on_rejection(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5C)

        session.handle_notification(
            make_packet(0xA9, bytes.fromhex("1122334455667788"), ProtocolFamily.V5C)
        )
        _controller(session).apply_compat_result(session, mode="auth", result_code=-2)

        self.assertTrue(_v5c_state(session).compatibility.checked)
        self.assertFalse(_v5c_state(session).compatibility.confirmed)
        self.assertEqual(_v5c_state(session).compatibility.last_result_code, -2)
        self.assertFalse(_v5c_state(session).compatibility.request_pending)
        self.assertIsNone(
            _controller(session).build_compat_request(
                ble_name="YTB01",
                ble_address="48:0F:57:49:1D:3A",
            )
        )
        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5C compatibility check failed")

    def test_v5c_error_status_warns_once_per_status_code(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5C)

        error_packet = make_packet(0xA1, bytes([0x04]), ProtocolFamily.V5C)
        session.handle_notification(error_packet)
        session.handle_notification(error_packet)

        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5C printer reported an overheat state")
        self.assertIn("status=0x04", warnings[0].detail)
        self.assertEqual(_v5c_state(session).status_name, "overheat")

    def test_v5c_attention_status_warns_and_uses_grouped_name(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5C)

        packet = make_packet(0xA1, bytes([0x01]), ProtocolFamily.V5C)
        session.handle_notification(packet)
        session.handle_notification(packet)

        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5C printer reported an attention state")
        self.assertIn("status=0x01", warnings[0].detail)
        self.assertEqual(_v5c_state(session).status_name, "attention")

    def test_v5c_normal_status_clears_previous_error_state(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        session.handle_notification(make_packet(0xA1, bytes([0x04]), ProtocolFamily.V5C))
        session.handle_notification(make_packet(0xA1, bytes([0x00]), ProtocolFamily.V5C))

        self.assertIsNone(_v5c_state(session).last_error_status)
        self.assertEqual(_v5c_state(session).status_name, "normal")

    def test_v5c_low_power_status_warns_once_per_status_code(self) -> None:
        session, _, sink = self._make_session_with_sink(ProtocolFamily.V5C)

        packet = make_packet(0xA1, bytes([0x08]), ProtocolFamily.V5C)
        session.handle_notification(packet)
        session.handle_notification(packet)

        warnings = [msg for msg in sink.messages if msg.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].short, "V5C printer reported a low-power state")
        self.assertIn("status=0x08", warnings[0].detail)
        self.assertEqual(_v5c_state(session).status_name, "low_power")

    def test_v5c_charging_status_sets_charging_flag(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        session.handle_notification(make_packet(0xA1, bytes([0x10]), ProtocolFamily.V5C))

        self.assertEqual(_v5c_state(session).status_name, "charging")
        self.assertTrue(_v5c_state(session).is_charging)
        self.assertIsNone(_v5c_state(session).last_error_status)

    def test_v5c_return_to_normal_marks_print_complete_after_printing(self) -> None:
        session, _ = self._make_session(ProtocolFamily.V5C)

        session.handle_notification(make_packet(0xA1, bytes([0x80]), ProtocolFamily.V5C))
        session.handle_notification(make_packet(0xA1, bytes([0x00]), ProtocolFamily.V5C))

        self.assertEqual(_v5c_state(session).status_name, "normal")
        self.assertTrue(_v5c_state(session).print_complete_seen)
        self.assertFalse(_v5c_state(session).is_charging)


if __name__ == "__main__":
    unittest.main()
