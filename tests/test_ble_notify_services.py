from __future__ import annotations

import asyncio
import socket
import unittest
from types import SimpleNamespace

from timiniprint import reporting
from timiniprint.devices.bluetooth_profiles import BleTransportProfile
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession
from timiniprint.transport.bluetooth.classic_receive import ClassicReceiveHub


def characteristic(uuid, *properties):
    return SimpleNamespace(uuid=uuid, properties=properties)


class Client:
    def __init__(self, fail_on=None):
        self.callbacks = {}
        self.stopped = []
        self.fail_on = fail_on

    async def start_notify(self, uuid, callback):
        if uuid == self.fail_on:
            raise RuntimeError("subscription failed")
        self.callbacks[uuid] = callback

    async def stop_notify(self, uuid):
        self.stopped.append(uuid)
        self.callbacks.pop(uuid, None)


def make_session():
    session = _BleakTransportSession(
        BleTransportProfile(notify_service_uuids=("service-a",)),
        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
    )
    session.configure_endpoints([
        SimpleNamespace(uuid="service-a", characteristics=[
            characteristic("channel-a", "notify"),
            characteristic("channel-b", "indicate"),
            characteristic("read-only", "read"),
        ]),
        SimpleNamespace(uuid="service-b", characteristics=[characteristic("unrelated", "notify")]),
    ])
    return session


class BleNotifyServicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_property_subscriptions_are_scoped_to_services_and_all_closed(self):
        session = make_session()
        client = Client()
        received = []
        await session.start_notify_if_available(client, lambda _sender, data: received.append(data))
        self.assertEqual(set(client.callbacks), {"channel-a", "channel-b"})
        client.callbacks["channel-b"]("channel-b", b"reply")
        self.assertEqual(received, [b"reply"])
        await session.stop_notify_if_started(client)
        self.assertEqual(set(client.stopped), {"channel-a", "channel-b"})
        self.assertFalse(session.notify_started)

    async def test_failed_subscription_closes_already_subscribed_channels(self):
        session = make_session()
        client = Client(fail_on="channel-b")
        with self.assertRaisesRegex(RuntimeError, "subscription failed"):
            await session.start_notify_if_available(client, lambda *_args: None)
        self.assertEqual(client.callbacks, {})
        self.assertEqual(client.stopped, ["channel-a"])
        self.assertFalse(session.notify_started)

    async def test_runtime_observes_bytes_before_passive_reply_predicate(self):
        session = make_session()
        client = Client()
        class Observer(RuntimeController):
            seen = False
            def handle_notification(self, session, payload):
                self.seen = payload == b"ready"
        observer = Observer()
        await session.attach_runtime_controller(observer, mtu_size=20, timeout=0.1)
        await session.start_notify_if_available(client, lambda _sender, data: session.handle_notification(data))
        waiter = asyncio.create_task(session.wait_for_notification(
            "ready", lambda data: observer.seen and data == b"ready", timeout=0.2,
        ))
        try:
            await asyncio.sleep(0)
            client.callbacks["channel-b"]("channel-b", b"ready")
            self.assertEqual(await waiter, b"ready")
        finally:
            await session.stop_notify_if_started(client)


class PhomemoNotifyProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_selection_uses_properties_in_preferred_service(self):
        from timiniprint.devices import get_ble_transport_profile
        from timiniprint.protocol.family import ProtocolFamily

        profile = get_ble_transport_profile(ProtocolFamily.PHOMEMO_ESC)
        for property_name in ("notify", "indicate"):
            with self.subTest(property=property_name):
                session = _BleakTransportSession(
                    profile, _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
                )
                reply = characteristic("reply-channel", property_name)
                session.configure_endpoints([
                    SimpleNamespace(uuid="unrelated", characteristics=[characteristic("other", "notify")]),
                    SimpleNamespace(uuid=profile.preferred_service_uuid, characteristics=[
                        characteristic(profile.preferred_write_char_uuid, "write-without-response"), reply,
                    ]),
                ])
                client = Client()
                await session.start_notify_if_available(client, lambda *_args: None)
                self.assertEqual(set(client.callbacks), {reply.uuid})
                await session.stop_notify_if_started(client)
                self.assertEqual(client.stopped, [reply.uuid])


class ClassicReplyObservationTests(unittest.TestCase):
    def test_listener_updates_state_before_passive_reply_predicate(self):
        reader, writer = socket.socketpair()
        seen = []
        hub = ClassicReceiveHub(reader, listener=seen.append)
        try:
            waiter = hub.register_passive(lambda data: bool(seen) and data == b"ready")
            hub.start()
            writer.sendall(b"ready")
            self.assertEqual(hub.wait(waiter, timeout=1.0), b"ready")
            self.assertEqual(seen, [b"ready"])
        finally:
            hub.stop()
            reader.close()
            writer.close()
