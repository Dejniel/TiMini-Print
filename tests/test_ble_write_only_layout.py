import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_ble_gatt_layouts import characteristic, service
from timiniprint import reporting
from timiniprint.devices import BleGattLayout, BleTransportProfile
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession


LAYOUT = BleGattLayout("declared-service", "declared-write", "")


@pytest.mark.parametrize("path", ["payload", "control"])
def test_write_only_layout_uses_exact_endpoint_without_subscribing(path):
    async def run():
        found = service(LAYOUT, reply=False)
        foreign = SimpleNamespace(uuid="other", characteristics=[characteristic("0000ff02-0000-1000-8000-00805f9b34fb", "write")])
        session = _BleakTransportSession(BleTransportProfile(gatt_layouts=(LAYOUT,)),
                                        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER)
        session.configure_endpoints([foreign, found])
        assert session.bindings.write_char is found.characteristics[0]
        assert not session.bindings.notify_char_uuid and not session.bindings.additional_notify_char_uuids
        client = SimpleNamespace(start_notify=AsyncMock(), write_gatt_char=AsyncMock())
        await session.start_notify_if_available(client, lambda *_: None)
        await session.initialize_connection(client, mtu_size=23, timeout=1)
        if path == "control":
            assert await session.send_control_packet(b"test")
        else:
            await session.send(client, b"test", mtu_size=23, timeout=1)
        client.start_notify.assert_not_called()
        assert client.write_gatt_char.await_args.args[0] is found.characteristics[0]
    asyncio.run(run())


def test_missing_write_cannot_fall_back_to_a_foreign_service():
    foreign = SimpleNamespace(uuid="other", characteristics=[characteristic("fallback", "write")])
    assert _BleWriteEndpointResolver().resolve([foreign], gatt_layouts=(LAYOUT,)) is None


def test_declared_reply_cannot_be_silently_dropped():
    assert _BleWriteEndpointResolver().resolve([service(LAYOUT, reply=False)],
        gatt_layouts=(BleGattLayout(LAYOUT.service_uuid, LAYOUT.write_char_uuid, "required-reply"),)) is None
