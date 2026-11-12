"""Broad GATT metadata subscriptions cannot veto a usable printer channel."""
import asyncio
from types import SimpleNamespace

import pytest

from tests.test_ble_notify_services import Client
from timiniprint import reporting
from timiniprint.devices.bluetooth_profiles import BleTransportProfile
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
            assert set(client.callbacks) == {"2a05", "vendor-reply"}
            client.callbacks["2a05"]("2a05", b"\x01\xff")
            client.callbacks["vendor-reply"]("vendor-reply", b"reply")
            assert received == [b"reply"]
        finally:
            await session.stop_notify_if_started(client)
        assert set(client.stopped) == {"2a05", "vendor-reply"}
    asyncio.run(run())


def test_failed_metadata_alone_does_not_claim_a_working_receiver():
    async def run():
        session = session_for(service("1801", "2a05"))
        client = Client(fail_on="2a05")
        await session.start_notify_if_available(client, lambda *_: None)
        assert not session.can_wait_for_notification()
        await session.stop_notify_if_started(client)
    asyncio.run(run())
