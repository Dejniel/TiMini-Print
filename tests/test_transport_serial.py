from __future__ import annotations

import asyncio
import builtins
import os
import select
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from timiniprint.devices import PrinterCatalog, SerialTarget
from timiniprint.devices.profiles import StreamProfile
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolStep
from timiniprint.transport.serial import SerialConnection, SerialConnector


class _SerialPort:
    """A bounded byte stream, including partial writes and read failures."""

    def __init__(self, *args, **kwargs):
        self.condition = threading.Condition()
        self.data = bytearray()
        self.writes = []
        self.closed = False
        self.close_error = None
        self.read_error = None
        self.write_error = None
        self.write_limit = None
        self.write_timeout = kwargs.get("write_timeout", 5.0)
        self.timeout = kwargs.get("timeout", 0.05)
        self.out_waiting = 0
        self.on_write = lambda _: None

    def feed(self, data):
        with self.condition:
            self.data.extend(data)
            self.condition.notify_all()

    @property
    def in_waiting(self):
        with self.condition:
            return len(self.data)

    def read(self, count):
        with self.condition:
            if not self.data:
                self.condition.wait(self.timeout)
            if self.read_error is not None:
                raise self.read_error
            value = bytes(self.data[:count])
            del self.data[:count]
            return value

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        value = bytes(data if self.write_limit is None else data[:self.write_limit])
        self.writes.append(value)
        self.on_write(value)
        return len(value)

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class _FlowController(RuntimeController):
    def __init__(self):
        self.received = []
        self.stopped = 0
        self.state_changed = threading.Event()

    def handle_notification(self, session, data):
        self.received.append(data)
        if data in (b"pause", b"resume"):
            session.set_flow_paused(data == b"pause")
        self.state_changed.set()

    async def stop(self, session):
        self.stopped += 1


class TransportSerialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        device = PrinterCatalog.load().device_from_profile("x6h", transport_target=SerialTarget("/dev/test"))
        self.device = device.with_print_profile(replace(device.profile, stream=StreamProfile(2, 0)))
        self.port = _SerialPort()
        self.factory_calls = []

        def open_port(*args, **kwargs):
            self.factory_calls.append((args, kwargs))
            return self.port

        self.patch = patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=open_port)})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def connect(self):
        connection = await SerialConnector().connect(self.device)
        self.addAsyncCleanup(connection.disconnect)
        return connection

    async def test_open_once_for_multiple_jobs_and_close_explicitly(self):
        connection = await self.connect()
        self.assertIsInstance(connection, SerialConnection)
        self.assertIsNone(connection.active_ble_profile)
        self.assertTrue(connection.can_query_control_packet())
        await connection.send(ProtocolJob(payload=b"abcdef"))
        await connection.send_standard_payload(b"gh")
        self.assertEqual(self.port.writes, [b"ab", b"cd", b"ef", b"gh"])
        self.assertEqual(len(self.factory_calls), 1)
        self.assertFalse(self.port.closed)
        await connection.disconnect()
        self.assertTrue(self.port.closed)
        self.assertFalse(connection.can_query_control_packet())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await connection.send_standard_payload(b"x")

    async def test_partial_writes_are_completed(self):
        connection = await self.connect()
        self.port.write_limit = 1
        await connection.send_standard_payload(b"abcd")
        self.assertEqual(self.port.writes, [b"a", b"b", b"c", b"d"])

    async def test_zero_write_is_an_error_not_a_busy_loop(self):
        connection = await self.connect()
        self.port.write_limit = 0
        with self.assertRaisesRegex(OSError, "no progress"):
            await connection.send_standard_payload(b"ab")

    async def test_immediate_fragmented_query_has_one_reader_and_updates_runtime_first(self):
        connection = await self.connect()
        controller = _FlowController()
        await connection.attach_runtime_controller(controller)

        def reply(_):
            self.port.feed(b"A")
            # A second fragment from the printer, not a second reader.
            timer = threading.Timer(0.01, lambda: self.port.feed(b"CK"))
            timer.start()
            self.addCleanup(timer.join)

        self.port.on_write = reply
        result = await connection.query_control_packet(
            b"query", timeout=1,
            reply_complete=lambda data: b"".join(controller.received) == data == b"ACK",
        )
        self.assertEqual(result, b"ACK")
        self.assertEqual(b"".join(controller.received), b"ACK")
        self.assertEqual(self.port.write_timeout, 5.0)

    async def test_timeout_preserves_partial_response(self):
        connection = await self.connect()
        self.port.on_write = lambda _: self.port.feed(b"A")
        self.assertEqual(await connection.query_control_packet(
            b"query", timeout=0.03, reply_complete=lambda data: data == b"ACK"), b"A")
        self.port.on_write = lambda _: None
        self.assertIsNone(await connection.query_control_packet(b"silent", timeout=0.02))

    async def test_passive_reply_does_not_write_or_replay_consumed_bytes(self):
        connection = await self.connect()
        self.port.feed(b"done")
        self.assertEqual(await connection.wait_for_reply("complete", lambda data: data == b"done", timeout=1), b"done")
        self.assertIsNone(await connection.wait_for_reply("next", bool, timeout=0.02, required=False))
        self.assertEqual(self.port.writes, [])

    async def test_passive_wait_does_not_block_a_concurrent_status_query(self):
        connection = await self.connect()
        observing = threading.Event()

        def completed(data):
            observing.set()
            return data.endswith(b"done")

        waiter = asyncio.create_task(connection.wait_for_reply("complete", completed, timeout=0.5, required=False))
        query = None
        try:
            self.port.feed(b"working")
            self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, observing.wait, 1))
            self.port.on_write = lambda _: self.port.feed(b"ACK")
            query = asyncio.create_task(connection.query_control_packet(b"status", timeout=0.1, reply_complete=bool))
            done, _ = await asyncio.wait((query,), timeout=0.2)
            self.assertIn(query, done, "Passive wait blocked the status query")
            self.assertEqual(await query, b"ACK")
        finally:
            self.port.feed(b"done")
            await asyncio.gather(waiter, *((query,) if query is not None else ()), return_exceptions=True)

    async def test_required_passive_matcher_is_not_evaluated_twice(self):
        connection = await self.connect()
        calls = []

        def one_match(data):
            calls.append(data)
            return len(calls) == 1

        self.port.feed(b"done")
        self.assertEqual(await connection.wait_for_reply("complete", one_match, timeout=0.2), b"done")
        self.assertEqual(calls, [b"done"])

    async def test_receive_failure_wakes_a_paused_writer(self):
        connection = await self.connect()
        controller = _FlowController()
        await connection.attach_runtime_controller(controller)
        self.port.feed(b"pause")
        self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, controller.state_changed.wait, 1))
        writer = asyncio.create_task(connection.send_standard_payload(b"image"))
        try:
            self.port.read_error = OSError("serial receive failed")
            self.port.feed(b"trigger")
            done, _ = await asyncio.wait((writer,), timeout=0.2)
            self.assertIn(writer, done, "Reader failure left the writer paused")
            with self.assertRaisesRegex(OSError, "serial receive failed"):
                await writer
            self.assertEqual(self.port.writes, [])
        finally:
            await connection.disconnect()
            await asyncio.gather(writer, return_exceptions=True)

    async def test_write_failure_removes_query_waiter(self):
        connection = await self.connect()
        self.port.write_error = OSError("write failed")
        with self.assertRaisesRegex(OSError, "write failed"):
            await connection.query_control_packet(b"query", reply_complete=lambda _: self.fail("stale waiter"))
        self.port.write_error = None
        self.port.on_write = lambda _: self.port.feed(b"ok")
        self.assertEqual(await connection.query_control_packet(b"query", reply_complete=bool), b"ok")

    async def test_read_failure_wakes_pending_query(self):
        connection = await self.connect()
        self.port.read_error = OSError("read failed")
        with self.assertRaisesRegex(OSError, "read failed"):
            await connection.query_control_packet(b"query", timeout=5, reply_complete=bool)

    async def test_notification_can_schedule_control_reply_in_application_loop(self):
        connection = await self.connect()
        app_loop = asyncio.get_running_loop()

        class Reactive(RuntimeController):
            task = None
            loop = None

            def handle_notification(self, session, payload):
                self.loop = asyncio.get_running_loop()
                if payload == b"challenge" and session.can_send_control_packet():
                    self.task = self.loop.create_task(session.send_control_packet(b"response"))

        controller = Reactive()
        await connection.attach_runtime_controller(controller)
        self.port.on_write = lambda data: self.port.feed(b"challenge") if data == b"query" else None
        self.assertEqual(await connection.query_control_packet(
            b"query", timeout=1, reply_complete=lambda _: controller.task is not None), b"challenge")
        self.assertIs(controller.loop, app_loop)
        self.assertTrue(await asyncio.wait_for(controller.task, 1))
        self.assertEqual(self.port.writes, [b"query", b"response"])

    async def test_close_failure_retains_port_for_retry(self):
        connection = await self.connect()
        self.port.close_error = OSError("close failed")
        with self.assertRaisesRegex(OSError, "close failed"):
            await connection.disconnect()
        self.assertFalse(self.port.closed)
        self.port.close_error = None
        await connection.disconnect()
        self.assertTrue(self.port.closed)

    async def test_disconnect_wakes_long_wait_and_paused_writer(self):
        connection = await self.connect()
        controller = _FlowController()
        await connection.attach_runtime_controller(controller)
        self.port.feed(b"pause")
        await asyncio.get_running_loop().run_in_executor(None, controller.state_changed.wait, 1)
        writer = asyncio.create_task(connection.send_standard_payload(b"abcd"))
        waiter = asyncio.create_task(connection.wait_for_reply("done", lambda _: False, timeout=60, required=False))
        await asyncio.sleep(0.02)
        await asyncio.wait_for(connection.disconnect(), 1)
        with self.assertRaisesRegex(RuntimeError, "disconnected|closed"):
            await writer
        await asyncio.wait_for(waiter, 1)
        self.assertEqual(controller.stopped, 1)
        self.assertEqual(self.port.writes, [])

    async def test_pause_resume_gates_each_chunk(self):
        connection = await self.connect()
        controller = _FlowController()
        await connection.attach_runtime_controller(controller)
        self.port.feed(b"pause")
        await asyncio.get_running_loop().run_in_executor(None, controller.state_changed.wait, 1)
        writer = asyncio.create_task(connection.send_standard_payload(b"abcd"))
        await asyncio.sleep(0.02)
        self.assertEqual(self.port.writes, [])
        self.port.feed(b"resume")
        await asyncio.wait_for(writer, 1)
        self.assertEqual(self.port.writes, [b"ab", b"cd"])

    async def test_step_job_uses_existing_tiny_pause_resume_controller(self):
        connection = await self.connect()

        async def connect(_):
            return connection

        printer = await connect_printer(self.device, SimpleNamespace(connect=connect))
        self.addAsyncCleanup(printer.disconnect)
        paused = bytes.fromhex("51 78 AE 01 01 00 10 70 FF")
        resumed = bytes.fromhex("51 78 AE 01 01 00 00 00 FF")
        self.port.feed(paused)
        await connection.wait_for_reply("pause observed", lambda data: paused in data, timeout=1)
        task = asyncio.create_task(printer.send_job(ProtocolJob(steps=(
            ProtocolStep.send("image", b"abcd"),
        ))))
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        self.assertEqual(self.port.writes, [])
        self.port.feed(resumed)
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.port.writes, [b"ab", b"cd"])

    async def test_controller_replacement_and_detach_stop_each_owner_once(self):
        connection = await self.connect()
        first, second = _FlowController(), _FlowController()
        await connection.attach_runtime_controller(first)
        await connection.attach_runtime_controller(first)
        await connection.attach_runtime_controller(second)
        await connection.attach_runtime_controller(None)
        self.assertEqual((first.stopped, second.stopped), (1, 1))
        await connection.disconnect()
        self.assertEqual((first.stopped, second.stopped), (1, 1))

    async def test_payload_and_step_jobs_through_connected_printer(self):
        self.port.on_write = lambda data: self.port.feed(b"OK") if data == b"query" else None
        printer = await connect_printer(self.device, SerialConnector())
        self.addAsyncCleanup(printer.disconnect)
        await printer.send_job(ProtocolJob(payload=b"abcd"))
        await printer.send_job(ProtocolJob(steps=(
            ProtocolStep.query("setup", b"query", expect=ProtocolReplyExpectation.OK, reply_required=True),
            ProtocolStep.send("image", b"efgh"),
        )))
        self.assertEqual(self.port.writes, [b"ab", b"cd", b"query", b"ef", b"gh"])

    async def test_direct_send_rejects_execution_steps(self):
        connection = await self.connect()
        with self.assertRaisesRegex(RuntimeError, "ConnectedPrinter.send_job"):
            await connection.send(ProtocolJob(steps=(ProtocolStep.send("image", b"abc"),)))

    async def test_connect_failure_is_reported_at_connect_not_first_print(self):
        def failing_open(*_, **__):
            raise OSError("open failed")

        with patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=failing_open)}):
            with self.assertRaisesRegex(OSError, "open failed"):
                await SerialConnector().connect(self.device)

    async def test_output_drain_is_bounded_for_payload_and_control(self):
        connection = await self.connect()
        self.port.out_waiting = 1
        with patch("timiniprint.transport.serial._WRITE_TIMEOUT_SEC", 0.03):
            with self.assertRaisesRegex(TimeoutError, "draining"):
                await connection.send_standard_payload(b"data")
            with self.assertRaisesRegex(TimeoutError, "draining"):
                await connection.send_control_packet(b"control", timeout=0.03)

    async def test_query_expires_while_a_payload_holds_the_native_writer(self):
        connection = await self.connect()
        started, release = threading.Event(), threading.Event()

        def blocked_write(_):
            started.set()
            release.wait(2)

        self.port.on_write = blocked_write
        writer = asyncio.create_task(connection.send_standard_payload(b"abcd"))
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
        self.assertEqual(self.port.writes, [b"ab", b"cd"])

    async def test_cancelled_write_finishes_before_close_and_sends_no_later_chunks(self):
        connection = await self.connect()
        started, release = threading.Event(), threading.Event()

        def write(_):
            started.set()
            release.wait(2)

        self.port.on_write = write
        task = asyncio.create_task(connection.send_standard_payload(b"abcd"))
        try:
            self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            close = asyncio.create_task(connection.disconnect())
            await asyncio.sleep(0.04)
            self.assertFalse(self.port.closed)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(close, 1)
            self.assertTrue(self.port.closed)
            self.assertEqual(self.port.writes, [b"ab"])
        finally:
            release.set()

    async def test_cancelled_open_closes_late_success(self):
        started, release = threading.Event(), threading.Event()

        def delayed_open(*_, **__):
            started.set()
            release.wait(2)
            return self.port

        with patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=delayed_open)}):
            task = asyncio.create_task(SerialConnector().connect(self.device))
            try:
                self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 1))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                release.set()

                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(self.port.closed)
            finally:
                release.set()

    async def test_cancelled_close_cannot_hide_cleanup_failure(self):
        connection = await self.connect()
        started, release = threading.Event(), threading.Event()

        def broken_close():
            started.set()
            release.wait(2)
            raise OSError("close failed")

        with patch.object(self.port, "close", side_effect=broken_close):
            task = asyncio.create_task(connection.disconnect())
            try:
                self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 1))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                release.set()
                with self.assertRaisesRegex(OSError, "close failed"):
                    await task
            finally:
                release.set()
        await connection.disconnect()
        self.assertTrue(self.port.closed)

    async def test_missing_pyserial(self):
        original_import = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "serial":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=importing):
            with self.assertRaisesRegex(RuntimeError, "pyserial is required"):
                await SerialConnector().connect(self.device)


@unittest.skipUnless(hasattr(os, "openpty"), "Requires local POSIX pseudo terminals")
class SerialPtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pyserial_query_payload_and_passive_receive_on_one_port(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        device = PrinterCatalog.load().device_from_profile("x6h", transport_target=SerialTarget(os.ttyname(slave)))
        connection = await SerialConnector().connect(device)
        self.addAsyncCleanup(connection.disconnect)
        received = bytearray()
        errors = []

        def peer():
            try:
                deadline = time.monotonic() + 2
                while b"query" not in received:
                    if time.monotonic() > deadline:
                        raise TimeoutError("No query on local serial test port")
                    if select.select([master], [], [], 0.05)[0]:
                        received.extend(os.read(master, 4096))
                os.write(master, b"O")
                time.sleep(0.01)
                os.write(master, b"K")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=peer, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        await connection.send(ProtocolJob(payload=b"image"))
        self.assertEqual(await connection.query_control_packet(
            b"query", timeout=1, reply_complete=lambda data: data == b"OK"), b"OK")
        self.assertEqual(received, b"imagequery")
        self.assertEqual(errors, [])
        os.write(master, b"done")
        reply = await connection.wait_for_reply("complete", lambda data: data.endswith(b"done"), timeout=1)
        self.assertTrue(reply.endswith(b"done"))
