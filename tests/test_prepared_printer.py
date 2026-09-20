from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpointTransport, SerialTarget
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
from timiniprint.protocol import ProtocolFamily, ProtocolJob, ProtocolStep
from timiniprint.protocol.runtime import RuntimePrintCapabilities


class Connection:
    def __init__(self):
        self.controllers = []
        self.sent = []
        self.closed = False
        self.queries = 0
        self.active_ble_profile = None

    async def attach_runtime_controller(self, controller, *, timeout):
        previous = self.controllers[-1] if self.controllers else None
        if previous is not None and previous is not controller:
            await previous.stop(self)
        self.controllers.append(controller)

    def can_query_control_packet(self):
        return True

    def can_send_standard_payload(self):
        return True

    async def query_control_packet(self, packet, **kwargs):
        self.queries += 1
        return b"\x2a"

    async def send_standard_payload(self, data):
        self.sent.append(data)

    async def send(self, job):
        self.sent.append(job.payload)

    async def disconnect(self):
        self.closed = True


class Connector:
    def __init__(self, *, ble=False):
        self.connections = []
        self.ble = ble

    async def connect(self, device):
        connection = Connection()
        connection.active_ble_profile = device.ble_transport_profile if self.ble else None
        self.connections.append(connection)
        return connection


class SelectedController(RuntimeController):
    def __init__(self, key):
        self.key = key
        self.completions = 0

    async def send_protocol_steps(self, session, steps, *, timeout):
        for step in steps:
            await session.send_standard_payload(bytes(value ^ self.key for value in step.data))
        return True

    async def wait_for_completion(self, session, *, timeout):
        self.completions += 1


class Bootstrap(RuntimeController):
    def __init__(self, *, failure=None, stateless=False):
        self.failure = failure
        self.stateless = stateless
        self.stopped = False
        self.selected = None

    async def prepare(self, device, session, *, timeout):
        if self.failure:
            raise self.failure
        reply = await session.query_control_packet(b"identify", timeout=timeout)
        self.selected = None if self.stateless else SelectedController(reply[0])
        selected = PrinterCatalog.load().device_from_profile("v5g_small_203").resolve_for_connection(device)
        return PreparedPrinter(selected, self.selected, RuntimePrintCapabilities(supports_gray=False))

    async def stop(self, session):
        self.stopped = True


def device():
    return PrinterCatalog.load().device_from_profile("x6h").with_transport_target(SerialTarget("/dev/test"))


@pytest.mark.parametrize("steps", [False, True])
def test_dynamic_selection_publishes_one_configuration_and_keeps_runtime_for_later_jobs(steps):
    async def run():
        connector = Connector()
        bootstrap = Bootstrap()
        initial = device()
        printer = await connect_printer(initial, connector, controller=bootstrap)
        connection = connector.connections[0]
        assert printer.printer_device().protocol_family is ProtocolFamily.V5G
        assert printer.printer_device().profile_key == "v5g_small_203"
        assert printer.printer_device().profile.protocol_default.type is ProtocolFamily.V5G
        assert initial.protocol_family is not ProtocolFamily.V5G
        assert printer.print_capabilities().supports_gray is False
        assert bootstrap.stopped
        assert connection.controllers == [bootstrap, bootstrap.selected]
        job = ProtocolJob(payload=b"raster", wait_for_completion=True,
                          steps=(ProtocolStep.send("page", b"raster"),) if steps else ())
        with patch("timiniprint.printing.runtime.prepare.runtime_controller_for_device",
                   side_effect=AssertionError("No factory calls during sending")):
            await printer.send_job(job)
            await printer.send_job(job)
        assert connection.queries == 1
        assert connection.controllers == [bootstrap, bootstrap.selected]
        assert bootstrap.selected.key == 42
        assert bootstrap.selected.completions == 2
        expected = bytes(value ^ 42 for value in b"raster") if steps else b"raster"
        assert connection.sent == [expected, expected]
        with pytest.raises(FrozenInstanceError):
            printer.printer_device().protocol_family = initial.protocol_family
        await printer.disconnect()
        assert connection.closed
    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError("unknown identity"), TimeoutError("no reply"), asyncio.CancelledError()])
def test_failed_preparation_closes_connection_without_sending(failure):
    async def run():
        connector = Connector()
        with pytest.raises(type(failure)) as caught:
            await connect_printer(device(), connector, controller=Bootstrap(failure=failure))
        assert caught.value is failure
        assert connector.connections[0].closed
        assert connector.connections[0].sent == []
    asyncio.run(run())


def test_stateless_selection_detaches_bootstrap():
    async def run():
        connector = Connector()
        bootstrap = Bootstrap(stateless=True)
        printer = await connect_printer(device(), connector, controller=bootstrap)
        assert connector.connections[0].controllers == [bootstrap, None]
        await printer.send_job(ProtocolJob(payload=b"raw"))
        assert connector.connections[0].sent == [b"raw"]
    asyncio.run(run())


def test_dynamic_family_cannot_replace_ble_bindings_on_an_open_connection():
    async def run():
        connector = Connector(ble=True)
        endpoint = BluetoothEndpoint(
            "X6H", "00:11:22:33:44:55", transport=BluetoothEndpointTransport.BLE,
        )
        initial = device().with_transport_target(BluetoothTarget(
            None, endpoint, endpoint.address, "[ble]",
        ))
        with pytest.raises(RuntimeError, match="ble_transport_profile"):
            await connect_printer(initial, connector, controller=Bootstrap())
        connection = connector.connections[0]
        assert len(connection.controllers) == 1
        assert connection.closed and not connection.sent
    asyncio.run(run())


def test_reconnect_uses_fresh_runtime_and_repeats_identification():
    async def run():
        connector = Connector()
        first, second = Bootstrap(), Bootstrap()
        printer = await connect_printer(device(), connector, controller=first)
        await printer.disconnect()
        printer = await connect_printer(device(), connector, controller=second)
        assert first.selected is not second.selected
        assert [connection.queries for connection in connector.connections] == [1, 1]
        await printer.disconnect()
    asyncio.run(run())
