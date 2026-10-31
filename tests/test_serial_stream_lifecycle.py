"""Transport lifecycle without a printer profile or physical serial port."""
import asyncio
import threading
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_transport_serial import _SerialPort, _FlowController
from timiniprint.devices import SerialTarget
from timiniprint.devices.profiles import StreamProfile
from timiniprint.transport.serial import SerialConnection


class SerialStreamLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = SerialTarget("/dev/test", 9600)
        self.port = _SerialPort()
        self.calls = []

        def open_port(*args, **kwargs):
            self.calls.append((args, kwargs))
            return self.port

        self.patcher = patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=open_port)})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def connection(self, *, receive=False):
        connection = SerialConnection(self.target, stream=StreamProfile(20, 0))
        self.addAsyncCleanup(connection.disconnect)
        await connection.open(receive=receive)
        return connection

    async def test_deferred_receiver_delivers_startup_bytes_to_its_first_observer(self):
        self.port.feed(b"startup")
        connection = await self.connection()
        seen = threading.Event()
        values = []

        def observe(data):
            values.append(data)
            seen.set()

        await connection.set_data_observer(observe)
        self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, seen.wait, 1))
        self.assertEqual(values, [b"startup"])
        self.assertEqual(self.calls[0][0], (self.target.path, self.target.baud_rate))
        self.assertFalse(self.port.writes)

    async def test_observer_and_runtime_ownership_are_exclusive(self):
        connection = await self.connection()
        await connection.set_data_observer(lambda data: None)
        controller = _FlowController()
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            await connection.attach_runtime_controller(controller)
        await connection.set_data_observer(None)
        await connection.attach_runtime_controller(controller)
        with self.assertRaisesRegex(RuntimeError, "owns reception"):
            await connection.set_data_observer(lambda data: None)
        with self.assertRaisesRegex(RuntimeError, "owns this serial"):
            await connection.quiesce()
        self.port.feed(b"runtime")
        self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, controller.state_changed.wait, 1))
        self.assertEqual(controller.received, [b"runtime"])

    async def test_quiesce_wakes_waiters_without_disposing_the_port(self):
        connection = await self.connection()
        await connection.set_data_observer(lambda data: None)
        waiting = asyncio.create_task(connection.wait_for_reply("idle", lambda data: False,
                                                               timeout=10, required=False))
        try:
            await asyncio.sleep(0.02)
            await connection.quiesce()
            await asyncio.wait_for(waiting, 1)
            self.assertFalse(connection.can_send_control_packet())
            self.assertFalse(self.port.closed)
            await connection.disconnect()
            self.assertTrue(self.port.closed)
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    async def test_timed_out_native_open_is_not_abandoned(self):
        entered, release = threading.Event(), threading.Event()

        def open_port(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release serial open")
            return self.port

        connection = SerialConnection(self.target, stream=StreamProfile(20, 0))
        self.addAsyncCleanup(connection.disconnect)
        with patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=open_port)}):
            opening = asyncio.create_task(connection.open(receive=False, timeout=0.01))
            try:
                self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, entered.wait, 1))
                await asyncio.sleep(0.03)
                self.assertFalse(opening.done())
                release.set()
                with self.assertRaises(TimeoutError):
                    await opening
            finally:
                release.set()
                await asyncio.gather(opening, return_exceptions=True)
        await connection.disconnect()
        self.assertTrue(self.port.closed)

    async def test_observer_swap_does_not_block_the_loop_needed_by_a_reply_predicate(self):
        connection = await self.connection(receive=True)
        entered = asyncio.Event()
        owner = asyncio.get_running_loop()
        accepted = Future()
        self.port.on_write = lambda data: self.port.feed(b"reply")

        def complete(data):
            owner.call_soon_threadsafe(entered.set)
            return accepted.result(timeout=1)

        query = asyncio.create_task(connection.query_control_packet(b"q", timeout=1, reply_complete=complete))
        swapping = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            swapping = asyncio.create_task(connection.set_data_observer(None))
            await asyncio.sleep(0)
            accepted.set_result(True)
            self.assertEqual(await query, b"reply")
            await swapping
        finally:
            if not accepted.done():
                accepted.set_result(False)
            await asyncio.gather(query, *((swapping,) if swapping is not None else ()), return_exceptions=True)

    async def test_open_connection_cannot_be_reopened(self):
        connection = await self.connection()
        with self.assertRaisesRegex(RuntimeError, "cannot be reopened"):
            await connection.open()
        self.assertEqual(len(self.calls), 1)
