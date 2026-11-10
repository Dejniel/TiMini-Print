"""Native connections share channel hints and keep same-service RX separated."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from timiniprint import reporting
from timiniprint.devices.bluetooth_channels import BLE_UART_READERS, BLE_UART_WRITERS, ble_uuid
from timiniprint.devices.bluetooth_profiles import BleGattLayout, BleTransportProfile
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession


def test_uuid_roles_support_short_and_full_uuid_without_guessing_unknown_channels():
    assert ble_uuid("FF02") in BLE_UART_WRITERS
    assert ble_uuid("ff01") in BLE_UART_READERS
    assert ble_uuid("unknown-write") not in BLE_UART_WRITERS
    assert ble_uuid("49535343-ACA3-481C-91EC-D85E28A60318") not in BLE_UART_WRITERS


def test_explicit_layout_can_listen_to_other_discovered_receivers_of_its_own_service():
    async def run():
        char = lambda uuid, *props: SimpleNamespace(uuid=uuid, properties=props)
        chosen = SimpleNamespace(uuid="vendor", characteristics=[
            char("write", "write"), char("reply-a", "notify"), char("reply-b", "indicate"),
        ])
        unrelated = SimpleNamespace(uuid="other", characteristics=[char("reply-c", "notify")])
        profile = BleTransportProfile(gatt_layouts=(BleGattLayout("vendor", "write", "reply-a"),),
                                      notify_service_uuids=("vendor", "other"))
        session = _BleakTransportSession(profile, _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER)
        session.configure_endpoints((chosen, unrelated))
        assert session.bindings.notify_char_uuid == "reply-a"
        assert session.bindings.additional_notify_char_uuids == ("reply-b",)
        client = SimpleNamespace(start_notify=AsyncMock(), stop_notify=AsyncMock(), write_gatt_char=AsyncMock())
        await session.start_notify_if_available(client, lambda *_: None)
        assert [call.args[0].uuid for call in client.start_notify.await_args_list] == ["reply-a", "reply-b"]
        await session.stop_notify_if_started(client)
        assert client.stop_notify.await_count == 2
    asyncio.run(run())
