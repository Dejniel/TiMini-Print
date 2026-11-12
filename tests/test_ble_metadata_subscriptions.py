"""Broad GATT metadata subscriptions cannot veto a usable printer channel."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_ble_notify_services import Client
from timiniprint import reporting
from timiniprint.devices.bluetooth_profiles import BleTransportProfile
from timiniprint.printing.runtime.phomemo import PhomemoRuntimeController
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession


def service(uuid, *channels):
    return SimpleNamespace(uuid=uuid, characteristics=[
        SimpleNamespace(uuid=channel, properties=("notify",), service_uuid=uuid)
        for channel in channels
    ])


def session_for(*services, profile=None):
    session = _BleakTransportSession(
        profile or BleTransportProfile(notify_all_services=True),
        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
    )
    session.configure_endpoints(services)
    return session


@pytest.mark.parametrize("metadata_service,metadata_channel", [("1801", "2a05"), ("180f", "2a19")])
@pytest.mark.parametrize("printer_first", [False, True])
def test_optional_metadata_failure_keeps_the_printer_reply_channel(metadata_service, metadata_channel, printer_first):
    async def run():
        # The subscription sort can place metadata before or after the printer.
        printer_channel = "00000001-vendor" if printer_first else "vendor-reply"
        session = session_for(service(metadata_service, metadata_channel), service("vendor", printer_channel))
        client = Client(fail_on=metadata_channel)
        received = []
        await session.start_notify_if_available(client, lambda _source, data: received.append(data))
        try:
            assert session.can_wait_for_notification()
            assert set(client.callbacks) == {printer_channel}
            client.callbacks[printer_channel](printer_channel, b"reply")
            assert received == [b"reply"]
        finally:
            await session.stop_notify_if_started(client)
        assert client.callbacks == {} and client.stopped == [printer_channel]
    asyncio.run(run())


def test_vendor_failure_still_aborts_and_closes_all_earlier_subscriptions():
    async def run():
        session = session_for(service("1801", "2a05"), service("vendor", "vendor-a", "vendor-b"))
        client = Client(fail_on="vendor-b")
        with pytest.raises(RuntimeError, match="subscription failed"):
            await session.start_notify_if_available(client, lambda *_: None)
        assert client.callbacks == {}
        assert set(client.stopped) == {"2a05", "vendor-a"}
        assert not session.can_wait_for_notification()
    asyncio.run(run())


def test_explicit_metadata_binding_remains_required():
    async def run():
        session = session_for(service("1801", "2a05"),
                              profile=BleTransportProfile(notify_service_uuids=("1801",)))
        with pytest.raises(RuntimeError, match="subscription failed"):
            await session.start_notify_if_available(Client(fail_on="2a05"), lambda *_: None)
        assert not session.can_wait_for_notification()
    asyncio.run(run())


def test_successful_metadata_is_still_subscribed_but_not_delivered_as_printer_bytes():
    async def run():
        session = session_for(service("1801", "2a05"), service("vendor", "vendor-reply"))
        client = Client()
        received = []
        await session.start_notify_if_available(client, lambda _source, data: received.append(data))
        try:
            assert session.can_wait_for_notification()
            assert set(client.callbacks) == {"2a05", "vendor-reply"}
            client.callbacks["2a05"]("2a05", b"\x01\xff")
            client.callbacks["vendor-reply"]("vendor-reply", b"reply")
            assert received == [b"reply"]
        finally:
            await session.stop_notify_if_started(client)
        assert set(client.stopped) == {"2a05", "vendor-reply"}
        assert not session.can_wait_for_notification()
    asyncio.run(run())


def test_failed_metadata_alone_does_not_claim_a_working_receiver():
    async def run():
        session = session_for(service("1801", "2a05"))
        client = Client(fail_on="2a05")
        await session.start_notify_if_available(client, lambda *_: None)
        assert not session.can_wait_for_notification()
        await session.stop_notify_if_started(client)
    asyncio.run(run())


@pytest.mark.parametrize("metadata_service,metadata_channel", [
    ("1800", "2a00"), ("1801", "2a05"), ("180a", "2a24"), ("180f", "2a19"),
])
def test_successful_metadata_alone_does_not_enable_protocol_waits_or_queries(metadata_service, metadata_channel):
    async def run():
        session = session_for(service(metadata_service, metadata_channel))
        client = Client()
        client.write_gatt_char = AsyncMock()
        session.bindings.write_char = SimpleNamespace(uuid="writer", properties=("write",))
        received = []
        await session.start_notify_if_available(client, lambda _source, data: received.append(data))
        await session.initialize_connection(client, mtu_size=20, timeout=0.1)
        try:
            assert session.notify_started  # The subscription must still be closed.
            assert session.can_send_control_packet()
            assert not session.can_wait_for_notification()
            assert not session.can_send_control_packet_wait_notification()
            assert not session.can_query_control_packet()
            client.callbacks[metadata_channel](metadata_channel, b"metadata")
            assert received == []
            assert await session.wait_for_notification("optional", bool, timeout=0, required=False) is None
            with pytest.raises(RuntimeError, match="notification wait unavailable"):
                await session.wait_for_notification("required", bool, timeout=0)
            assert await session.send_control_packet_wait_notification(
                b"query", label="optional", match=bool, timeout=0, required=False,
            ) is None
            with pytest.raises(RuntimeError, match="notification query unavailable"):
                await session.send_control_packet_wait_notification(
                    b"query", label="required", match=bool, timeout=0,
                )
            assert await session.query_control_packet(b"query", timeout=0) is None
            client.write_gatt_char.assert_not_awaited()
        finally:
            await session.stop_notify_if_started(client)
        assert client.stopped == [metadata_channel] and client.callbacks == {}
        assert not session.notify_started and not session.can_wait_for_notification()
    asyncio.run(run())


def test_control_only_subscription_does_not_enable_printer_reply_waits():
    async def run():
        session = session_for(service("vendor", "control"), profile=BleTransportProfile(
            notify_service_uuids=("vendor",), control_notify_char_uuid="control",
        ))
        client = Client()
        await session.start_notify_if_available(client, lambda *_: None)
        try:
            assert session.can_observe_control_notifications()
            assert not session.can_wait_for_notification()
        finally:
            await session.stop_notify_if_started(client)
        assert client.stopped == ["control"]
        assert not session.can_observe_control_notifications()
    asyncio.run(run())


def test_phomemo_requires_a_printer_receiver_not_only_metadata():
    async def run():
        session = session_for(service("180f", "2a19"))
        client = Client()
        await session.start_notify_if_available(client, lambda *_: None)
        try:
            with pytest.raises(RuntimeError, match="requires an active notification receiver"):
                await session.attach_runtime_controller(PhomemoRuntimeController(), mtu_size=20, timeout=0.1)
        finally:
            await session.stop_notify_if_started(client)
        assert client.stopped == ["2a19"] and client.callbacks == {}
    asyncio.run(run())


def test_explicit_metadata_channel_can_still_be_used_as_a_reply_binding():
    async def run():
        session = session_for(service("180f", "2a19"),
                              profile=BleTransportProfile(notify_service_uuids=("180f",)))
        client = Client()
        received = []
        await session.start_notify_if_available(client, lambda _source, data: received.append(data))
        try:
            assert session.can_wait_for_notification()
            client.callbacks["2a19"]("2a19", b"reply")
            assert received == [b"reply"]
        finally:
            await session.stop_notify_if_started(client)
        assert not session.can_wait_for_notification()
    asyncio.run(run())


def test_metadata_and_vendor_instances_with_the_same_uuid_keep_their_roles():
    async def run():
        session = session_for(service("180f", "2a19"), service("vendor", "2a19"))
        client = SimpleNamespace(start_notify=AsyncMock(), stop_notify=AsyncMock())
        received = []
        await session.start_notify_if_available(client, lambda _source, data: received.append(data))
        try:
            assert session.can_wait_for_notification()
            assert client.start_notify.await_count == 2
            for call in client.start_notify.await_args_list:
                target, callback = call.args
                callback(target, target.service_uuid.encode())
            assert received == [b"vendor"]
        finally:
            await session.stop_notify_if_started(client)
        assert [call.args[0] for call in client.stop_notify.await_args_list] == [
            call.args[0] for call in client.start_notify.await_args_list
        ]
        assert not session.can_wait_for_notification()
    asyncio.run(run())
