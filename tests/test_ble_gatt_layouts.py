from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from timiniprint import reporting
from timiniprint.devices import BleGattLayout, BleTransportProfile
from timiniprint.devices.bluetooth_profiles import BleBulkWriteProfile
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession

LAYOUTS = (
    BleGattLayout("service-a", "write-a", "reply-a"),
    BleGattLayout("service-b", "write-b", "reply-b"),
)
CONTROL = "control"


def characteristic(uuid, *properties):
    return SimpleNamespace(uuid=uuid, properties=properties)


def service(layout, *, write=True, reply=True, control=False):
    chars = []
    if write:
        chars.append(characteristic(layout.write_char_uuid, "write-without-response"))
    if reply:
        chars.append(characteristic(layout.notify_char_uuid, "notify"))
    if control:
        chars.append(characteristic(CONTROL, "notify"))
    return SimpleNamespace(uuid=layout.service_uuid, characteristics=chars)


@pytest.mark.parametrize("services,chosen", [
    ([service(LAYOUTS[1]), service(LAYOUTS[0])], 0),
    ([service(LAYOUTS[0], reply=False), service(LAYOUTS[1])], 1),
    ([service(LAYOUTS[0], write=False), service(LAYOUTS[1])], 1),
    ([service(LAYOUTS[0], reply=False), service(LAYOUTS[1], write=False)], None),
    ([SimpleNamespace(uuid="unknown", characteristics=[
        characteristic("unknown-write", "write"), characteristic("unknown-reply", "notify"),
    ])], None),
])
def test_layout_order_and_complete_service_pairs(services, chosen):
    resolver = _BleWriteEndpointResolver()
    selection = resolver.resolve(services, gatt_layouts=LAYOUTS)
    if chosen is None:
        assert selection is None
    else:
        assert selection.layout == LAYOUTS[chosen]
        assert selection.service_uuid == LAYOUTS[chosen].service_uuid
        assert selection.char_uuid == LAYOUTS[chosen].write_char_uuid


@pytest.mark.parametrize("role,properties", [
    ("write", ("read",)), ("write", ("notify",)), ("reply", ("read",)),
])
def test_layout_requires_actual_characteristic_properties(role, properties):
    found = service(LAYOUTS[0])
    target = LAYOUTS[0].write_char_uuid if role == "write" else LAYOUTS[0].notify_char_uuid
    next(char for char in found.characteristics if char.uuid == target).properties = properties
    assert _BleWriteEndpointResolver().resolve([found], gatt_layouts=LAYOUTS) is None


def test_layout_configuration_is_shared_by_direct_session_callers():
    profile = BleTransportProfile(gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL)
    resolver = _BleWriteEndpointResolver()
    session = _BleakTransportSession(profile, resolver, reporting.DUMMY_REPORTER)
    partial = service(LAYOUTS[0], write=False, control=True)
    selected = service(LAYOUTS[1])
    # An earlier generic write selection must not leak its service's notifications.
    session.configure_endpoints([partial, selected])
    assert session.bindings.write_service_uuid == LAYOUTS[1].service_uuid
    assert session.bindings.notify_char_uuid == LAYOUTS[1].notify_char_uuid
    assert session.bindings.additional_notify_char_uuids == ()
    with pytest.raises(RuntimeError, match="complete.*GATT"):
        other = _BleakTransportSession(profile, resolver, reporting.DUMMY_REPORTER)
        other.configure_endpoints([partial])


@pytest.mark.parametrize("path", ["payload", "control", "bulk"])
@pytest.mark.parametrize("write_property", ["write", "write-without-response"])
def test_layout_uses_existing_send_paths_and_optional_control(path, write_property):
    async def run():
        found = service(LAYOUTS[0], control=True)
        found.characteristics[0].properties = (write_property,)
        profile = BleTransportProfile(
            gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL,
            standard_chunk_cap=2, standard_write_delay_ms=0,
            bulk_write=BleBulkWriteProfile(LAYOUTS[0].write_char_uuid, 2, 0),
        )
        session = _BleakTransportSession(profile, _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER)
        session.configure_endpoints([found])
        client = SimpleNamespace(start_notify=AsyncMock(), stop_notify=AsyncMock(), write_gatt_char=AsyncMock())
        await session.start_notify_if_available(client, lambda *args: None)
        await session.initialize_connection(client, mtu_size=20, timeout=1)
        assert session.can_observe_control_notifications()
        subscribed = [call.args[0] for call in client.start_notify.await_args_list]
        assert len(subscribed) == 2
        assert subscribed[0] is found.characteristics[1]
        assert subscribed[1] is found.characteristics[2]
        if path == "control":
            assert await session.send_control_packet(b"abc")
        elif path == "bulk":
            assert await session.send_bulk_payload(client, b"abc", mtu_size=20)
        else:
            await session.send(client, b"abc", mtu_size=20, timeout=1)
        assert [call.args[1] for call in client.write_gatt_char.await_args_list] == [b"ab", b"c"]
        assert all(call.kwargs["response"] == (write_property == "write")
                   for call in client.write_gatt_char.await_args_list)
        await session.stop_notify_if_started(client)
        assert client.stop_notify.await_count == 2
    asyncio.run(run())


def test_layout_does_not_subscribe_to_non_notifying_control():
    found = service(LAYOUTS[0])
    found.characteristics.append(characteristic(CONTROL, "write"))
    session = _BleakTransportSession(
        BleTransportProfile(gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL),
        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
    )
    session.configure_endpoints([found])
    assert session.bindings.additional_notify_char_uuids == ()


def test_repeated_service_uuid_does_not_mix_service_instances():
    partial = service(LAYOUTS[0], write=False)
    complete = service(LAYOUTS[0])
    session = _BleakTransportSession(
        BleTransportProfile(gatt_layouts=LAYOUTS),
        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
    )
    session.configure_endpoints([partial, complete])
    assert session.bindings.write_char is complete.characteristics[0]
    assert session.bindings.notify_char is complete.characteristics[1]


@pytest.mark.parametrize("properties", [("notify",), (" NOTIFY ",)])
def test_layout_subscriptions_keep_the_selected_characteristic_instances(properties):
    async def run():
        partial = service(LAYOUTS[0], write=False, control=True)
        complete = service(LAYOUTS[0], control=True)
        complete.characteristics[1].properties = properties
        complete.characteristics[2].properties = properties
        session = _BleakTransportSession(
            BleTransportProfile(gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL),
            _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
        )
        session.configure_endpoints([partial, complete])
        received = []

        async def start_notify(target, callback):
            # UUID lookup is ambiguous when both service instances contain RX.
            assert any(target is char for char in complete.characteristics[1:])
            callback(target, b"data")

        client = SimpleNamespace(start_notify=AsyncMock(side_effect=start_notify), stop_notify=AsyncMock())
        await session.start_notify_if_available(client, lambda *args: received.append(args))
        assert received == [(LAYOUTS[0].notify_char_uuid, b"data"), (CONTROL, b"data")]
        assert session.can_observe_control_notifications()
        await session.stop_notify_if_started(client)
        targets = [call.args[0] for call in client.stop_notify.await_args_list]
        assert len(targets) == 2
        assert targets[0] is complete.characteristics[1]
        assert targets[1] is complete.characteristics[2]

    asyncio.run(run())


def test_failed_layout_control_subscription_cleans_up_the_selected_rx():
    async def run():
        found = service(LAYOUTS[0], control=True)
        session = _BleakTransportSession(
            BleTransportProfile(gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL),
            _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER,
        )
        session.configure_endpoints([found])

        async def start_notify(target, callback):
            if target is found.characteristics[2]:
                raise RuntimeError("Control subscription failed")
            assert target is found.characteristics[1]

        client = SimpleNamespace(start_notify=AsyncMock(side_effect=start_notify), stop_notify=AsyncMock())
        with pytest.raises(RuntimeError, match="Control subscription failed"):
            await session.start_notify_if_available(client, lambda *args: None)
        assert client.stop_notify.await_count == 1
        assert client.stop_notify.await_args.args[0] is found.characteristics[1]
        assert not session.notify_started and not session.can_observe_control_notifications()
        await session.stop_notify_if_started(client)
        assert client.stop_notify.await_count == 1

    asyncio.run(run())


def test_gatt_layout_metadata_round_trips_through_json():
    original = BleTransportProfile(gatt_layouts=LAYOUTS, control_notify_char_uuid=CONTROL)
    restored = model_from_json(BleTransportProfile, json.loads(json.dumps(model_to_json(original))))
    assert restored == original
    assert replace(restored).gatt_layouts[1].notify_char_uuid == "reply-b"
