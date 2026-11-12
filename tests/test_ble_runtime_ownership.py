"""Runtime ownership and validation across all BLE shutdown paths."""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.test_phomemo_ble import BleConnection
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.printing.runtime.phomemo import PhomemoRuntimeController
from timiniprint.printing.runtime.session import RuntimeConnectionSession


class BleRuntimeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_cleanup_does_not_cancel_or_lose_native_disconnect_stop(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Controller(RuntimeController):
            calls = 0
            async def stop(self, session):
                self.calls += 1
                entered.set()
                await release.wait()
        link = await BleConnection().open()
        controller = Controller()
        await link.attach_runtime_controller(controller, timeout=.02)
        link.io.connection_lost()
        await entered.wait()
        cleanup = asyncio.create_task(link.disconnect())
        try:
            await asyncio.sleep(0)
            cleanup.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cleanup
            pending = link.io._disconnected_runtime_task
            self.assertIsNotNone(pending)
            self.assertFalse(pending.cancelled())
            self.assertFalse(pending.done())
        finally:
            release.set()
            await link.disconnect()
        self.assertEqual(controller.calls, 1)
        self.assertIsNone(link.io._disconnected_runtime_task)

    async def test_failed_replacement_retains_controller_for_cleanup_retry(self):
        class Controller(RuntimeController):
            fail = True
            async def stop(self, session):
                if self.fail:
                    raise OSError("stop failed")
        link = await BleConnection().open()
        old = Controller()
        await link.attach_runtime_controller(old, timeout=.02)
        try:
            with self.assertRaisesRegex(OSError, "stop failed"):
                await link.attach_runtime_controller(RuntimeController(), timeout=.02)
            self.assertIs(link.io._runtime_controller, old)
        finally:
            old.fail = False
            await link.disconnect()

    async def test_link_lost_during_initialization_never_publishes_controller(self):
        for phase in ("initialize", "after_initialize"):
            class Controller(RuntimeController):
                stops = 0
                async def initialize_connection(self, session, *, mtu_size, timeout):
                    if phase == "initialize":
                        session.connection_lost()
                async def after_initialize(self, session, *, timeout):
                    if phase == "after_initialize":
                        session.connection_lost()
                        await asyncio.sleep(0)
                async def stop(self, session):
                    self.stops += 1
            link = await BleConnection().open()
            controller = Controller()
            try:
                with self.assertRaisesRegex(ConnectionError, "lost"):
                    await link.attach_runtime_controller(controller, timeout=.02)
                self.assertIsNone(link.io._runtime_controller)
                self.assertEqual(controller.stops, 1)
            finally:
                await link.disconnect()

    async def test_failed_initialization_cleanup_retains_provisional_controller(self):
        class Controller(RuntimeController):
            fail = True
            async def initialize_connection(self, session, *, mtu_size, timeout):
                raise ValueError("initialization failed")
            async def stop(self, session):
                if self.fail:
                    raise OSError("stop failed")
        link = await BleConnection().open()
        controller = Controller()
        try:
            with self.assertRaisesRegex(ValueError, "initialization failed"):
                await link.attach_runtime_controller(controller, timeout=.02)
            self.assertIs(link.io._runtime_controller, controller)
        finally:
            controller.fail = False
            await link.disconnect()

    async def test_m02h_reset_cannot_precede_ble_runtime_validation(self):
        device = replace(PrinterCatalog.load().device_from_model("phomemo_m02"),
                         display_name="M02H", protocol_variant="m02h")
        connection = SimpleNamespace(active_ble_profile=device.ble_transport_profile,
                                     send_standard_payload=AsyncMock())
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        with self.assertRaisesRegex(RuntimeError, "initialization"):
            await PhomemoRuntimeController().prepare(device, session, timeout=.02)
        connection.send_standard_payload.assert_not_awaited()
