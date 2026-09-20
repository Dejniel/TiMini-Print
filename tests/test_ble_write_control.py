import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from timiniprint import reporting
from timiniprint.devices.bluetooth_profiles import BleBulkWriteProfile, BleTransportProfile
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.printing.step_execution import execute_protocol_step
from timiniprint.protocol import ProtocolStep
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession

WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"
REPLY = "0000ff01-0000-1000-8000-00805f9b34fb"
CONTROL = "0000ff03-0000-1000-8000-00805f9b34fb"


async def make_session(*, control=True, profile=None):
    profile = profile or BleTransportProfile(
        preferred_write_char_uuid=WRITE, notify_char_uuid=REPLY,
        control_notify_char_uuid=CONTROL, standard_chunk_cap=2,
        standard_write_delay_ms=0,
        bulk_write=BleBulkWriteProfile(WRITE, chunk_cap=2, write_delay_ms=0),
    )
    chars = [SimpleNamespace(uuid=WRITE, properties=["write"]),
             SimpleNamespace(uuid=REPLY, properties=["notify"])]
    if control:
        chars.append(SimpleNamespace(uuid=CONTROL, properties=["notify"]))
    session = _BleakTransportSession(profile, _BleWriteEndpointResolver(reporter=reporting.DUMMY_REPORTER),
                                     reporting.DUMMY_REPORTER)
    session.bindings.write_char = chars[0]
    session.configure_endpoints([SimpleNamespace(uuid="service", characteristics=chars)])
    client = SimpleNamespace(write_gatt_char=AsyncMock(), start_notify=AsyncMock(), stop_notify=AsyncMock())
    await session.start_notify_if_available(client, lambda source, data: session.handle_notification(data, source))
    await session.initialize_connection(client, mtu_size=23, timeout=1)
    return session, client


class Controller(RuntimeController):
    def __init__(self, fail=False):
        self.sizes, self.replies, self.controls = [], [], []
        self.fail = fail

    async def before_write(self, session, *, size, timeout):
        self.sizes.append(size)
        if self.fail and len(self.sizes) == 2:
            raise TimeoutError("no credit")

    def handle_notification(self, session, payload):
        self.replies.append(payload)

    def handle_control_notification(self, session, payload):
        self.controls.append(payload)


@pytest.mark.parametrize("path", ["payload", "steps", "control", "bulk"])
@pytest.mark.parametrize("fail", [False, True])
def test_write_hook_runs_once_per_physical_chunk_on_every_path(path, fail):
    async def run():
        session, client = await make_session()
        controller = Controller(fail)
        await session.attach_runtime_controller(controller, mtu_size=23, timeout=1)
        async def send():
            if path == "payload":
                await session.send(client, b"12345", mtu_size=23, timeout=1)
            elif path == "control":
                await session.send_control_packet(b"12345")
            elif path == "steps":
                async def write(data):
                    await session.send(client, data, mtu_size=23, timeout=1)
                steps_session = SimpleNamespace(send_standard_payload=write, report_debug=lambda _: None)
                await execute_protocol_step(
                    steps_session, ProtocolStep.send("page", b"12345"), timeout=1,
                )
            else:
                assert await session.send_bulk_payload(client, b"12345", mtu_size=23)
        if fail:
            with pytest.raises(TimeoutError, match="no credit"):
                await send()
        else:
            await send()
        assert controller.sizes == ([2, 2] if fail else [2, 2, 1])
        assert client.write_gatt_char.await_count == (1 if fail else 3)
    asyncio.run(run())


def test_control_channel_stays_separate_during_replay_and_waits():
    async def run():
        session, client = await make_session()
        assert session.can_observe_control_notifications()
        callbacks = {call.args[0]: call.args[1] for call in client.start_notify.await_args_list}
        callbacks[CONTROL](123, b"credit")  # Integer backend handle must retain bound UUID.
        callbacks[REPLY](456, b"reply")
        controller = Controller()
        await session.attach_runtime_controller(controller, mtu_size=23, timeout=1)
        assert controller.controls == [b"credit"]
        assert controller.replies == [b"reply"]
        assert await session.wait_for_notification("old credit", lambda p: p == b"credit",
                                                   timeout=0, required=False) is None
        task = asyncio.create_task(session.wait_for_notification(
            "fresh credit", lambda p: p == b"credit", timeout=0.01, required=False))
        await asyncio.sleep(0)
        callbacks[CONTROL](123, b"credit")
        assert await task is None
        assert controller.controls == [b"credit", b"credit"]
        assert controller.replies == [b"reply"]
    asyncio.run(run())


def test_absent_control_characteristic_is_not_enabled():
    async def run():
        session, client = await make_session(control=False)
        assert not session.can_observe_control_notifications()
        assert [c.args[0] for c in client.start_notify.await_args_list] == [REPLY]
    asyncio.run(run())


def test_present_control_subscription_failure_cannot_silently_disable_flow_control():
    async def run():
        session, client = await make_session()
        await session.stop_notify_if_started(client)
        async def subscribe(uuid, callback):
            if uuid == CONTROL:
                raise RuntimeError("subscription failed")
        client.start_notify = AsyncMock(side_effect=subscribe)
        with pytest.raises(RuntimeError, match="subscription failed"):
            await session.start_notify_if_available(client, lambda *args: None)
        assert not session.notify_started
        assert not session.can_observe_control_notifications()
    asyncio.run(run())
