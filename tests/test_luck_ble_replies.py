"""Luck BLE fallback must receive the replies required by its print jobs."""
import asyncio

import pytest

from tests.test_bleak_transport_session import _Char, _Client, _StandardRuntimeSession, _Svc
from tests.test_luck_transactions import page
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolStepOperation
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession


REPLY = "reply-channel"


class ReplyClient(_Client):
    def __init__(self, services, replies):
        super().__init__(services)
        self.replies = replies

    async def write_gatt_char(self, char, chunk, response=True):
        await super().write_gatt_char(char, chunk, response=response)
        reply = self.replies.get(bytes(chunk))
        if reply is not None:
            # Emit before the write returns: the query must already be armed.
            for byte in reply:
                self.notify_callbacks[REPLY](REPLY, bytes([byte]))


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("key", ["luck_a2", "luck_a40"])
@pytest.mark.parametrize("reply_property", ["notify", "indicate", None])
@pytest.mark.parametrize("notification_only", [False, True])
def test_ble_fallback_executes_required_queries_only_with_a_reply_channel(
    catalog, key, reply_property, notification_only,
):
    device = catalog.device_from_profile(key)
    job = page(device)
    replies = {step.data: b"\x00" if step.label == "status" else b"OK"
               for step in job.steps if step.operation is ProtocolStepOperation.QUERY}
    services = [_Svc("printer-service", [
        _Char("write-channel", ["write-without-response"]),
        _Char(REPLY, [reply_property] if reply_property else ["read"]),
    ])]
    client = ReplyClient(services, replies)
    resolver = _BleWriteEndpointResolver()
    profile = device.ble_transport_profile
    transport = _BleakTransportSession(profile, resolver, reporting.DUMMY_REPORTER)
    selection = resolver.resolve(services)
    assert selection is not None
    transport.apply_write_selection(selection)
    transport.configure_endpoints(services)

    class Connection(_StandardRuntimeSession):
        def can_query_control_packet(self):
            return not notification_only and transport.can_query_control_packet()

        async def send(self, job):
            pytest.fail("Required Luck replies must not be bypassed by stream-only sending")

    async def run():
        await transport.start_notify_if_available(
            client, lambda sender, data: transport.handle_notification(data, source=sender),
        )
        await transport.initialize_connection(client, mtu_size=20, timeout=.1)
        try:
            prepared = PreparedPrinter(device, runtime_controller_for_device(device))
            connection = Connection(transport, client)
            if reply_property is None:
                assert not transport.can_wait_for_notification()
                with pytest.raises(RuntimeError, match="requires protocol replies"):
                    await send_prepared_job(prepared, connection, job, timeout=.1)
                assert client.calls == []
            else:
                await send_prepared_job(prepared, connection, job, timeout=.1)
                assert set(client.notify_callbacks) == {REPLY}
                assert b"".join(call[1] for call in client.calls) == b"".join(
                    step.data for step in job.steps
                )
        finally:
            await transport.stop_notify_if_started(client)
        assert not client.notify_callbacks
        assert client.stop_notify_calls == ([REPLY] if reply_property else [])

    asyncio.run(run())
