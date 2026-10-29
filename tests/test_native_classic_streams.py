"""Native SPP callback adapters exercised without Windows/macOS hardware."""
from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from timiniprint import reporting
from timiniprint.devices import BluetoothEndpoint, BluetoothTarget, PrinterCatalog
from timiniprint.devices.profiles import StreamProfile
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolStep
from timiniprint.transport.bluetooth.adapters import macos_iobluetooth, windows_winrt
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.connector import BleakBluetoothConnection
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class _Reader:
    def __init__(self, stream):
        stream.reader = self
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.pending = 0
        self.closed = False

    def feed(self, data):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, data)

    async def load_async(self, count):
        self.pending += 1
        try:
            item = await self.queue.get()
            if isinstance(item, Exception):
                raise item
            self.data = item
            return len(item)
        finally:
            self.pending -= 1

    def read_bytes(self, output):
        output[:] = self.data

    def close(self):
        assert self.pending == 0, "reader disposed during a native read"
        self.closed = True


class _Writer:
    def __init__(self, stream):
        self.stream = stream
        self.closed = False

    def write_bytes(self, data):
        self.data = bytes(data)

    async def store_async(self):
        self.stream.sent.append(self.data)
        self.stream.on_write(self.data)

    async def flush_async(self):
        return True

    def close(self):
        self.closed = True


class _WinSocket:
    def __init__(self):
        self.sent = []
        self.on_write = lambda _: None
        self.input_stream = self
        self.output_stream = self
        self.closed = False
        self.connect_error = None
        self.close_error = None

    async def connect_async(self, host, service):
        if self.connect_error is not None:
            raise self.connect_error

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class _NSObject:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class _MacChannel:
    def __init__(self):
        self.sent = []
        self.incoming = queue.Queue()
        self.delegate = None
        self.closed = False
        self.on_write = lambda _: None
        self.close_status = 0
        self.owner_threads = []

    def setDelegate_(self, delegate):
        self.owner_threads.append(threading.get_ident())
        self.delegate = delegate
        return 0

    def writeSync_length_(self, data, length):
        self.owner_threads.append(threading.get_ident())
        self.sent.append(bytes(data[:length]))
        self.on_write(bytes(data[:length]))
        return 0

    def closeChannel(self):
        self.owner_threads.append(threading.get_ident())
        if self.close_status == 0:
            self.closed = True
        return self.close_status

    def poll(self):
        while self.delegate is not None:
            try:
                data = self.incoming.get_nowait()
            except queue.Empty:
                return
            if data is None:
                self.delegate.rfcommChannelClosed_(self)
            else:
                self.delegate.rfcommChannelData_data_length_(self, data, len(data))


class _ClassicAdapter:
    def __init__(self, sock):
        self.sock = sock

    def ensure_paired(self, *_):
        pass

    def resolve_rfcomm_channels(self, *_):
        return [1]

    def create_socket(self, *_, **__):
        return self.sock


class NativeClassicStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def winrt(self):
        native = _WinSocket()

        async def resolve(*_, **__):
            return SimpleNamespace(connection_host_name="host", connection_service_name="spp")

        sock = windows_winrt._WinRtSocket(SimpleNamespace(_resolve_service_async=resolve))
        self.stack.enter_context(patch.object(windows_winrt, "_winrt_imports", return_value=(
            None, None, None, None, lambda: native, _Writer, _Reader,
            SimpleNamespace(PARTIAL=1),
        )))
        self.addCleanup(sock.close)
        return sock, native, lambda data: native.reader.feed(data)

    def macos(self):
        native = _MacChannel()
        device = SimpleNamespace(
            openRFCOMMChannelSync_withChannelID_delegate_=lambda *_: (0, native),
            closeConnection=lambda: 0,
        )
        sock = macos_iobluetooth._MacClassicSocket(SimpleNamespace(get_device=lambda *_, **__: device))
        self.stack.enter_context(patch.object(macos_iobluetooth, "_iobluetooth_imports", return_value=(
            SimpleNamespace(autorelease_pool=contextlib.nullcontext), SimpleNamespace(NSObject=_NSObject),
        )))
        self.stack.enter_context(patch.object(macos_iobluetooth, "_rfcomm_delegate_class", None))
        self.stack.enter_context(patch.object(macos_iobluetooth, "_poll_rfcomm_run_loop", side_effect=native.poll))
        self.addCleanup(sock.close)
        return sock, native, native.incoming.put

    async def connect(self, sock):
        backend = SppBackend()
        self.stack.enter_context(patch("timiniprint.transport.bluetooth.backend._select_adapter",
                                       return_value=_ClassicAdapter(sock)))
        await backend.connect(DeviceInfo("X6H", "AA:BB:CC:DD:EE:FF"))
        self.addAsyncCleanup(backend.disconnect)
        endpoint = BluetoothEndpoint("X6H", "AA:BB:CC:DD:EE:FF")
        device = PrinterCatalog.load().device_from_profile("x6h").with_transport_target(
            BluetoothTarget(endpoint, None, endpoint.address, "[classic]"),
        )
        device = device.with_print_profile(replace(device.profile, stream=StreamProfile(2, 0)))
        connection = BleakBluetoothConnection(backend, device, reporting.DUMMY_REPORTER)
        connector = SimpleNamespace(connect=lambda _: _ready(connection))
        printer = await connect_printer(device, connector)
        return printer, connection

    async def exercise(self, factory):
        sock, native, feed = factory()
        printer, connection = await self.connect(sock)
        self.assertTrue(connection.can_query_control_packet())
        self.assertTrue(connection.can_wait_for_reply())
        native.on_write = lambda data: [feed(b"O"), feed(b"K")] if data == b"query" else None
        self.assertEqual(await connection.query_control_packet(
            b"query", timeout=1, reply_complete=lambda data: data == b"OK"), b"OK")
        await printer.send_job(ProtocolJob(payload=b"abcd"))
        await printer.send_job(ProtocolJob(steps=(
            ProtocolStep.query("setting", b"query", expect=ProtocolReplyExpectation.OK, reply_required=True),
            ProtocolStep.send("raster", b"efgh"),
        )))
        self.assertEqual(native.sent, [b"query", b"ab", b"cd", b"query", b"ef", b"gh"])
        feed(b"done")
        self.assertTrue((await connection.wait_for_reply(
            "passive", lambda data: data.endswith(b"done"), timeout=1)).endswith(b"done"))
        self.assertIsNone(await connection.query_control_packet(b"silent", timeout=0.02))
        await printer.disconnect()
        self.assertTrue(native.closed)

    async def test_winrt_payload_steps_queries_and_passive_replies(self):
        await self.exercise(self.winrt)

    async def test_macos_payload_steps_queries_and_passive_replies(self):
        await self.exercise(self.macos)

    async def flow(self, factory, *, steps=False):
        sock, native, feed = factory()
        printer, connection = await self.connect(sock)
        # Pause and resume are parsed by the existing Tiny controller, not by
        # either native adapter or the shared byte inbox.
        paused = bytes.fromhex("51 78 AE 01 01 00 10 70 FF")
        resumed = bytes.fromhex("51 78 AE 01 01 00 00 00 FF")
        feed(paused)
        await connection.wait_for_reply("pause observed", lambda data: paused in data, timeout=1)
        job = ProtocolJob(steps=(ProtocolStep.send("image", b"abcd"),)) if steps else ProtocolJob(payload=b"abcd")
        task = asyncio.create_task(printer.send_job(job))
        await asyncio.sleep(0.04)
        self.assertFalse(task.done())
        self.assertEqual(native.sent, [])
        feed(resumed)
        await asyncio.wait_for(task, 1)
        self.assertEqual(native.sent, [b"ab", b"cd"])

    async def test_winrt_flow_control(self):
        await self.flow(self.winrt)

    async def test_macos_flow_control(self):
        await self.flow(self.macos)

    async def test_winrt_step_job_flow_control(self):
        await self.flow(self.winrt, steps=True)

    async def test_macos_step_job_flow_control(self):
        await self.flow(self.macos, steps=True)

    async def test_native_write_cancellation_does_not_dispose_an_active_writer(self):
        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, _ = factory()
                printer, connection = await self.connect(sock)
                started, release = threading.Event(), threading.Event()

                if factory.__name__ == "winrt":
                    original = _Writer.store_async

                    async def store(writer):
                        started.set()
                        while not release.is_set():
                            await asyncio.sleep(0.005)
                        await original(writer)

                    self.stack.enter_context(patch.object(_Writer, "store_async", store))
                else:
                    def store(data):
                        started.set()
                        release.wait(2)

                    native.on_write = store

                task = asyncio.create_task(printer.send_job(ProtocolJob(payload=b"abcd")))
                try:
                    self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 1))
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    close = asyncio.create_task(connection.disconnect())
                    await asyncio.sleep(0.04)
                    self.assertFalse(native.closed)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    await asyncio.wait_for(close, 1)
                    self.assertTrue(native.closed)
                    self.assertEqual(native.sent, [b"ab"])
                finally:
                    release.set()

    async def test_native_runtime_callback_error_is_reported_to_query(self):
        class Broken(RuntimeController):
            def handle_notification(self, session, payload):
                raise ValueError("runtime callback failed")

        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, feed = factory()
                _, connection = await self.connect(sock)
                await connection.attach_runtime_controller(Broken())
                native.on_write = lambda _: feed(b"data")
                with self.assertRaisesRegex(ValueError, "runtime callback failed"):
                    await connection.query_control_packet(b"query", timeout=5, reply_complete=bool)

    async def test_runtime_reaction_uses_application_loop_and_existing_control_sender(self):
        app_loop = asyncio.get_running_loop()

        class Reactive(RuntimeController):
            task = None
            loop = None

            def handle_notification(self, session, payload):
                self.loop = asyncio.get_running_loop()
                if payload == b"challenge" and session.can_send_control_packet():
                    self.task = self.loop.create_task(session.send_control_packet(b"response"))

        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, feed = factory()
                _, connection = await self.connect(sock)
                controller = Reactive()
                await connection.attach_runtime_controller(controller)
                native.on_write = lambda data: feed(b"challenge") if data == b"query" else None
                self.assertEqual(await connection.query_control_packet(
                    b"query", timeout=1, reply_complete=lambda _: controller.task is not None), b"challenge")
                self.assertIs(controller.loop, app_loop)
                self.assertTrue(await asyncio.wait_for(controller.task, 1))
                self.assertEqual(native.sent, [b"query", b"response"])

    async def test_winrt_failed_connect_releases_partial_native_resources(self):
        sock, native, _ = self.winrt()
        native.connect_error = OSError("connect failed")
        with self.assertRaisesRegex(RuntimeError, "connect failed"):
            await self.connect(sock)
        self.assertTrue(native.closed)
        self.assertIsNone(sock._thread)

    async def test_winrt_close_cancels_read_before_disposing_reader(self):
        sock, native, _ = self.winrt()
        _, connection = await self.connect(sock)
        reader = native.reader
        self.assertEqual(reader.input_stream_options, 1)
        await connection.disconnect()
        self.assertTrue(reader.closed)
        self.assertEqual(reader.pending, 0)
        self.assertIsNone(sock._thread)

    async def test_winrt_read_failure_wakes_query_and_is_not_a_timeout(self):
        sock, native, feed = self.winrt()
        _, connection = await self.connect(sock)
        native.on_write = lambda _: feed(OSError("native read failed"))
        with self.assertRaisesRegex(OSError, "native read failed"):
            await connection.query_control_packet(b"query", timeout=5, reply_complete=bool)

    async def test_macos_delegate_runs_on_channel_owner_thread_and_detaches(self):
        sock, native, feed = self.macos()
        _, connection = await self.connect(sock)
        feed(b"reply")
        await connection.wait_for_reply("reply", bool, timeout=1)
        await connection.send_control_packet(b"write")
        await connection.disconnect()
        self.assertEqual(len(set(native.owner_threads)), 1)
        self.assertNotEqual(native.owner_threads[0], threading.get_ident())
        self.assertIsNone(native.delegate)
        self.assertIsNone(sock._thread)

    async def test_macos_remote_close_wakes_query(self):
        sock, native, feed = self.macos()
        _, connection = await self.connect(sock)
        native.on_write = lambda _: feed(None)
        with self.assertRaisesRegex(ConnectionError, "peer closed"):
            await connection.query_control_packet(b"query", timeout=5, reply_complete=bool)

    async def test_receive_failure_interrupts_paused_payload_and_step_jobs(self):
        paused = bytes.fromhex("51 78 AE 01 01 00 10 70 FF")
        for factory in (self.winrt, self.macos):
            for steps in (False, True):
                with self.subTest(platform=factory.__name__, steps=steps):
                    sock, native, feed = factory()
                    printer, connection = await self.connect(sock)
                    feed(paused)
                    await connection.wait_for_reply("pause", lambda data: paused in data, timeout=1)
                    job = ProtocolJob(steps=(ProtocolStep.send("image", b"abcd"),)) if steps else ProtocolJob(payload=b"abcd")
                    writer = asyncio.create_task(printer.send_job(job))
                    try:
                        feed(OSError("receiver failed") if factory.__name__ == "winrt" else None)
                        done, _ = await asyncio.wait((writer,), timeout=0.3)
                        self.assertIn(writer, done, "Reader failure did not release paused writer")
                        with self.assertRaisesRegex(OSError, "receiver failed|peer closed"):
                            await writer
                        with self.assertRaisesRegex(OSError, "receiver failed|peer closed"):
                            await connection.send_control_packet(b"must not be sent")
                        self.assertEqual(native.sent, [])
                    finally:
                        await connection.disconnect()
                        await asyncio.gather(writer, return_exceptions=True)

    async def test_required_wait_does_not_accept_an_unmatched_partial_reply(self):
        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, _, feed = factory()
                _, connection = await self.connect(sock)
                feed(b"partial")
                with self.assertRaisesRegex(TimeoutError, "complete"):
                    await connection.wait_for_reply("complete", lambda data: data.endswith(b"done"), timeout=0.03)

    async def test_query_write_budget_is_temporary(self):
        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, feed = factory()
                _, connection = await self.connect(sock)
                budgets = []

                def reply(_):
                    budgets.append(sock.gettimeout())
                    feed(b"ok")

                previous = sock.gettimeout()
                native.on_write = reply
                self.assertEqual(await connection.query_control_packet(b"query", timeout=0.3, reply_complete=bool), b"ok")
                self.assertTrue(0 < budgets[0] <= 0.3)
                self.assertEqual(sock.gettimeout(), previous)

    async def test_query_expires_while_the_native_writer_is_busy(self):
        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, _ = factory()
                printer, connection = await self.connect(sock)
                started, release = threading.Event(), threading.Event()

                def blocked_write(_):
                    started.set()
                    release.wait(2)

                native.on_write = blocked_write
                writer = asyncio.create_task(printer.send_job(ProtocolJob(payload=b"abcd")))
                query = None
                try:
                    self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 1))
                    query = asyncio.create_task(connection.query_control_packet(b"must not be sent", timeout=0.03))
                    done, _ = await asyncio.wait((query,), timeout=0.2)
                    self.assertIn(query, done)
                    with self.assertRaises(TimeoutError):
                        await query
                finally:
                    release.set()
                    await asyncio.gather(writer, *((query,) if query is not None else ()), return_exceptions=True)
                self.assertEqual(native.sent, [b"ab", b"cd"])

    async def test_native_close_failure_remains_retryable(self):
        for factory in (self.winrt, self.macos):
            with self.subTest(platform=factory.__name__):
                sock, native, _ = factory()
                _, connection = await self.connect(sock)
                if factory.__name__ == "winrt":
                    native.close_error = OSError("close failed")
                else:
                    native.close_status = 7
                with self.assertRaisesRegex((OSError, RuntimeError), "close failed"):
                    await connection.disconnect()
                self.assertFalse(native.closed)
                if factory.__name__ == "winrt":
                    native.close_error = None
                else:
                    native.close_status = 0
                await connection.disconnect()
                self.assertTrue(native.closed)

    async def test_macos_run_loop_fault_keeps_native_cleanup_on_its_owner_thread(self):
        sock, native, _ = self.macos()
        failed = threading.Event()

        def poll():
            if failed.is_set():
                raise RuntimeError("native run loop failed")
            native.poll()

        with patch.object(macos_iobluetooth, "_poll_rfcomm_run_loop", side_effect=poll):
            _, connection = await self.connect(sock)
            failed.set()
            with self.assertRaisesRegex(RuntimeError, "native run loop failed"):
                await connection.wait_for_reply("receive failure", lambda _: False, timeout=1)
            native.close_status = 7
            try:
                with self.assertRaisesRegex(RuntimeError, "close failed"):
                    await connection.disconnect()
            finally:
                native.close_status = 0
                await connection.disconnect()
            self.assertTrue(native.closed)
            self.assertIsNone(native.delegate)
            self.assertIsNone(sock._thread)
            self.assertEqual(len(set(native.owner_threads)), 1)


async def _ready(value):
    return value
