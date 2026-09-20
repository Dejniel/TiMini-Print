from __future__ import annotations

import asyncio
from dataclasses import replace
import socket
import threading
import warnings

import pytest

from tests.test_bleak_transport_session import _Char, _Client, _Svc
from tests.test_bluetooth_backend_connect import _QuerySocket
from timiniprint import reporting
from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpointTransport, SerialTarget
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
from timiniprint.printing.runtime.v5x import V5XRuntimeController
from timiniprint.protocol import ProtocolFamily, ProtocolJob, ProtocolStep
from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.connector import BleakBluetoothConnection
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
from timiniprint.transport.serial import SerialConnection

_SERVICE = '0000ff00-0000-1000-8000-00805f9b34fb'
_WRITE = '0000ff02-0000-1000-8000-00805f9b34fb'
_NOTIFY = '0000ff01-0000-1000-8000-00805f9b34fb'


class _Gate:
    """Hold an adapter operation until the application loop releases it."""

    def __init__(self, *, fail=False):
        self.app_loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.loop = None
        self.future = None
        self.fail = fail

    async def wait(self):
        self.loop = asyncio.get_running_loop()
        self.future = self.loop.create_future()
        self.app_loop.call_soon_threadsafe(self.started.set)
        await self.future
        if self.fail:
            raise RuntimeError('setup failed')

    def release(self):
        if self.future is not None:
            self.loop.call_soon_threadsafe(self._release)

    def _release(self):
        if not self.future.done():
            self.future.set_result(None)


class _PrinterClient(_Client):
    def __init__(self):
        super().__init__([_Svc(_SERVICE, [
            _Char(_WRITE, ['write-without-response']), _Char(_NOTIFY, ['notify']),
        ])])
        self.gate = None
        self.disconnected = False

    async def write_gatt_char(self, char, chunk, response=True):
        if self.gate is not None:
            await self.gate.wait()
        await super().write_gatt_char(char, chunk, response=response)
        callback = self.notify_callbacks.get(_NOTIFY)
        if callback is not None:
            callback(_NOTIFY, b'ack')

    async def disconnect(self):
        self.disconnected = True


def _device(*, dual=False):
    ble = BluetoothEndpoint('X6H', '00:11:22:33:44:55', transport=BluetoothEndpointTransport.BLE)
    classic = BluetoothEndpoint('X6H', ble.address) if dual else None
    return PrinterCatalog.load().device_from_profile('x6h').with_transport_target(
        BluetoothTarget(classic, ble, ble.address, '[classic+ble]' if dual else '[ble]'),
    )


class _Link:
    def __init__(self, device):
        self.device = device
        self.client = _PrinterClient()
        self.loop = asyncio.new_event_loop()
        self.socket = _BleakSocket(ble_profile=device.ble_transport_profile)
        self.socket._loop = self.loop
        self.socket._client = self.client
        self.socket._connected = True
        self.backend = SppBackend()
        self.backend._sock = self.socket
        self.backend._connected = True
        self.backend._transport = DeviceTransport.BLE
        self.connection = BleakBluetoothConnection(self.backend, device, reporting.DUMMY_REPORTER)

    async def connect(self, device):
        assert device == self.device
        await asyncio.get_running_loop().run_in_executor(None, self._configure)
        return self.connection

    def _configure(self):
        transport = self.socket._transport
        transport.apply_write_selection(self.socket._write_resolver.resolve(self.client.services))
        transport.configure_endpoints(self.client.services)
        self.loop.run_until_complete(transport.start_notify_if_available(self.client, self.socket._handle_notification))
        self.loop.run_until_complete(transport.initialize_connection(self.client, mtu_size=20, timeout=0.1))


class _Setup(RuntimeController):
    def __init__(self, gate, phase):
        self.gate, self.phase = gate, phase
        self.stopped = 0
        self.loop = None

    async def initialize_connection(self, session, *, mtu_size, timeout):
        self.loop = asyncio.get_running_loop()
        if self.phase == 'initialize':
            await self.gate.wait()

    async def prepare(self, device, session, *, timeout):
        if self.phase == 'query':
            assert await session.query_control_packet(b'query', timeout=timeout) == b'ack'
        elif self.phase == 'notify':
            assert await session.send_control_packet_wait_notification(
                b'query', label='ack', match=lambda raw: raw == b'ack', timeout=timeout,
            ) == b'ack'
        return PreparedPrinter(device, self)

    async def stop(self, session):
        assert asyncio.get_running_loop() is self.loop
        self.stopped += 1


@pytest.mark.parametrize('phase', ['initialize', 'notify'])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('cancel_twice', [False, True])
def test_cancelled_preparation_finishes_adapter_io_before_closing(phase, failure, cancel_twice):
    async def run():
        device = _device()
        link = _Link(device)
        gate = _Gate(fail=failure)
        controller = _Setup(gate, phase)
        if phase != 'initialize':
            link.client.gate = gate
        task = asyncio.create_task(connect_printer(device, link, controller=controller, timeout=2))
        try:
            await asyncio.wait_for(gate.started.wait(), 3)
            task.cancel()
            await asyncio.sleep(0.01)
            if cancel_twice:
                task.cancel()
                await asyncio.sleep(0.01)
            assert not task.done()
            assert not link.client.disconnected
            gate.release()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert link.client.disconnected
            assert link.loop.is_closed()
            assert not link.backend.is_connected()
            assert controller.stopped == 1
        finally:
            if not task.done():
                gate.release()
            await asyncio.gather(task, return_exceptions=True)
            await link.connection.disconnect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        asyncio.run(run())
    assert not [entry for entry in caught if issubclass(entry.category, RuntimeWarning)]


@pytest.mark.parametrize('reply', [True, False])
def test_cancelled_classic_query_waits_for_reply_or_timeout_before_cleanup(reply):
    async def run():
        local, remote = socket.socketpair()
        remote.setblocking(False)
        backend = SppBackend()
        backend._sock = local
        backend._connected = True
        backend._transport = DeviceTransport.CLASSIC
        task = asyncio.create_task(backend.query_control_packet(b'query', timeout=0.2))
        try:
            assert await asyncio.get_running_loop().sock_recv(remote, 5) == b'query'
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            if reply:
                await asyncio.get_running_loop().sock_sendall(remote, b'ack')
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            await asyncio.gather(task, return_exceptions=True)
            await backend.disconnect()
            remote.close()
        assert local.fileno() == -1
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['initialize', 'after_initialize', 'prepare'])
@pytest.mark.parametrize('stop_fails', [False, True])
def test_failed_setup_cleans_up_once_and_preserves_original_error(phase, stop_fails):
    class FailingSetup(RuntimeController):
        stopped = 0

        async def initialize_connection(self, session, *, mtu_size, timeout):
            self.owner = asyncio.get_running_loop()
            if phase == 'initialize':
                raise ValueError('original setup error')

        async def after_initialize(self, session, *, timeout):
            if phase == 'after_initialize':
                raise ValueError('original setup error')

        async def prepare(self, device, session, *, timeout):
            raise ValueError('original setup error')

        async def stop(self, session):
            assert asyncio.get_running_loop() is self.owner
            self.stopped += 1
            if stop_fails:
                raise RuntimeError('cleanup error')

    async def run():
        device = _device()
        link = _Link(device)
        controller = FailingSetup()
        with pytest.raises(ValueError, match='original setup error'):
            await connect_printer(device, link, controller=controller)
        assert controller.stopped == 1
        assert link.client.disconnected and link.loop.is_closed()
        assert not link.backend.is_connected()
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('close_fails', [False, True])
def test_failed_or_cancelled_connect_closes_socket_without_masking_error(cancel, close_fails):
    async def run():
        backend = SppBackend()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        released = threading.Event()
        local, remote = socket.socketpair()

        class Socket:
            def close(self):
                local.close()
                if close_fails:
                    raise RuntimeError('close failed')

        def connecting(attempts, pairing_hint):
            backend._sock = Socket()
            backend._connected = True
            loop.call_soon_threadsafe(started.set)
            if not released.wait(3):
                raise AssertionError('test did not release connect')
            if not cancel:
                raise ValueError('connect failed')

        backend._connect_attempts_blocking = connecting
        task = asyncio.create_task(backend.connect(DeviceInfo('test', '00:11:22:33:44:55')))
        try:
            await asyncio.wait_for(started.wait(), 2)
            if cancel:
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and local.fileno() != -1
            released.set()
            expected = asyncio.CancelledError if cancel else ValueError
            with pytest.raises(expected, match=None if cancel else 'connect failed'):
                await task
            assert not backend.is_connected()
            assert local.fileno() == -1
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)
            local.close()
            remote.close()
    asyncio.run(run())


class _ReadyController(RuntimeController):
    def __init__(self):
        self.key = 42
        self.notifications = []
        self.stopped = 0

    async def initialize_connection(self, session, *, mtu_size, timeout):
        raise AssertionError('A ready replacement must not be initialized again')

    def handle_notification(self, session, payload):
        self.notifications.append(payload)

    async def stop(self, session):
        self.stopped += 1


class _Bootstrap(V5XRuntimeController):
    def __init__(self, replacement):
        super().__init__()
        self.replacement = replacement
        self.owner = None
        self.stopped = 0
        self.task = None

    async def initialize_connection(self, session, *, mtu_size, timeout):
        self.owner = asyncio.get_running_loop()
        self.task = asyncio.create_task(asyncio.sleep(60))
        self._state.pending_sign_responses.add(self.task)

    async def after_initialize(self, session, *, timeout):
        pass

    async def prepare(self, device, session, *, timeout):
        selected = replace(device, protocol_family=ProtocolFamily.TINY_PREFIXED,
                           profile=replace(device.profile, protocol_default=replace(
                               device.profile.protocol_default, type=ProtocolFamily.TINY_PREFIXED)))
        return PreparedPrinter(selected, self.replacement)

    async def stop(self, session):
        assert asyncio.get_running_loop() is self.owner
        await super().stop(session)
        self.stopped += 1


@pytest.mark.parametrize('stateless', [False, True])
@pytest.mark.parametrize('steps', [False, True])
def test_replacement_stops_pending_tasks_in_ble_loop_and_preserves_ready_state(stateless, steps):
    async def run():
        device = _device()
        link = _Link(device)
        selected = None if stateless else _ReadyController()
        bootstrap = _Bootstrap(selected)
        printer = await connect_printer(device, link, controller=bootstrap)
        assert bootstrap.stopped == 1 and bootstrap.task.cancelled()
        assert printer.printer_device().protocol_family is ProtocolFamily.TINY_PREFIXED
        job = ProtocolJob(payload=b'page', steps=(ProtocolStep.send('page', b'page'),) if steps else ())
        async with printer:
            await printer.send_job(job)
            await printer.send_job(job)
        assert [data for _, data, _ in link.client.calls] == [b'page', b'page']
        assert link.client.disconnected and link.loop.is_closed()
        assert bootstrap.stopped == 1
        if selected is not None:
            assert selected.key == 42 and selected.stopped == 1
            assert selected.notifications == [b'ack', b'ack']
    asyncio.run(run())


@pytest.mark.parametrize('adapter_owned', [False, True])
def test_classic_or_custom_replacement_stops_controller_in_application_loop(adapter_owned):
    class Bootstrap(RuntimeController):
        stopped = 0

        async def prepare(self, device, session, *, timeout):
            self.owner = asyncio.get_running_loop()
            self.task = asyncio.create_task(asyncio.sleep(60))
            return PreparedPrinter(device)

        async def stop(self, session):
            assert asyncio.get_running_loop() is self.owner
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.stopped += 1

    async def run():
        device = _device(dual=True)
        local, remote = socket.socketpair()
        backend = SppBackend()
        backend._sock, backend._connected = local, True
        backend._transport = DeviceTransport.CLASSIC

        class Connection:
            async def disconnect(self):
                await backend.disconnect()

        connection = (BleakBluetoothConnection(backend, device, reporting.DUMMY_REPORTER)
                      if adapter_owned else Connection())

        class Connector:
            async def connect(self, device):
                return connection

        bootstrap = Bootstrap()
        try:
            printer = await connect_printer(device, Connector(), controller=bootstrap)
            assert bootstrap.stopped == 1 and bootstrap.task.cancelled()
            await printer.disconnect()
            assert bootstrap.stopped == 1
            assert local.fileno() == -1
        finally:
            await backend.disconnect()
            remote.close()
    asyncio.run(run())


class _SelectProfile(RuntimeController):
    async def prepare(self, device, session, *, timeout):
        selected = PrinterCatalog.load().device_from_profile('v5g_small_203').resolve_for_connection(device)
        return PreparedPrinter(selected, RuntimeController())


@pytest.mark.parametrize('active', [DeviceTransport.CLASSIC, DeviceTransport.BLE])
def test_family_selection_uses_actual_fallback_transport_not_discovery_endpoints(active):
    async def run():
        device = _device(dual=True)
        backend = SppBackend()
        failed = DeviceTransport.BLE if active is DeviceTransport.CLASSIC else DeviceTransport.CLASSIC
        attempts = []
        local, remote = socket.socketpair()

        def connect_attempt(candidate, pairing_hint):
            attempts.append(candidate.transport)
            if candidate.transport is failed:
                raise RuntimeError('first transport unavailable')
            backend._sock = local if candidate.transport is DeviceTransport.CLASSIC else _QuerySocket([])
            backend._connected = True
            backend._transport = candidate.transport

        backend._connect_with_device = connect_attempt
        await backend.connect_attempts([
            DeviceInfo('X6H', device.address, transport=failed),
            DeviceInfo('X6H', device.address, transport=active),
        ])
        connection = BleakBluetoothConnection(backend, device, reporting.DUMMY_REPORTER)

        class Connector:
            async def connect(self, selection):
                return connection

        try:
            if active is DeviceTransport.CLASSIC:
                assert connection.active_ble_profile is None
                printer = await connect_printer(device, Connector(), controller=_SelectProfile())
                assert printer.printer_device().profile_key == 'v5g_small_203'
            else:
                assert connection.active_ble_profile == device.ble_transport_profile
                with pytest.raises(RuntimeError, match='ble_transport_profile'):
                    await connect_printer(device, Connector(), controller=_SelectProfile())
            assert attempts == [failed, active]
        finally:
            await connection.disconnect()
            local.close()
            remote.close()
    asyncio.run(run())


def test_serial_reports_no_ble_bindings():
    device = _device().with_transport_target(SerialTarget('/dev/test'))
    assert SerialConnection(device).active_ble_profile is None


def test_unknown_custom_transport_cannot_silently_change_gatt_profile():
    class Connection:
        async def disconnect(self):
            pass

    class Connector:
        async def connect(self, device):
            return Connection()

    with pytest.raises(RuntimeError, match='connection.active_ble_profile'):
        asyncio.run(connect_printer(_device(), Connector(), controller=_SelectProfile()))
