"""Phomemo BLE session contracts; simulated GATT, not hardware validation."""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog, get_ble_transport_profile
from timiniprint.devices.bluetooth_profiles import BleBulkWriteProfile, BleGattLayout
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.printing.runtime.phomemo import PhomemoRuntimeController
from timiniprint.protocol import ProtocolJob
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.phomemo_esc.flow import PhomemoPageStep
from timiniprint.protocol.families.phomemo_esc.replies import PhomemoBleReplyDecoder
from timiniprint.transport.bluetooth.adapters.bleak_adapter_endpoint_resolver import _BleWriteEndpointResolver
from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import _BleakTransportSession


FF02 = "0000ff02-0000-1000-8000-00805f9b34fb"
SERIAL = b"Q17112345678901"


class BleConnection:
    """A custom connector exercising the ordinary runtime-aware print API."""

    def __init__(self, *, grant=b"\x01\x01", limit=182, fragment=False, pacing=0,
                 reporter=reporting.DUMMY_REPORTER):
        self.active_ble_profile = replace(
            get_ble_transport_profile(ProtocolFamily.PHOMEMO_ESC), standard_write_delay_ms=pacing,
        )
        self.writer = SimpleNamespace(uuid=FF02, properties=["write-without-response"],
                                      max_write_without_response_size=512)
        self.services = [SimpleNamespace(uuid="vendor-service", characteristics=[
            self.writer, SimpleNamespace(uuid="reply", properties=["notify"]),
        ])]
        self.io = _BleakTransportSession(self.active_ble_profile, _BleWriteEndpointResolver(), reporter)
        self.callbacks = {}
        self.calls = []
        self.commands = []
        self.command_buffer = bytearray()
        self.fragment = fragment
        self.grant = grant
        self.limit = limit
        self.replenish = True
        self.is_connected = True
        self.pages = []
        self.page_data = bytearray()
        self.host_ready = asyncio.Event()
        self.host_ready.set()
        self.fail_write = False
        self.replies = {
            0x07: bytes.fromhex("1a07010203"), 0x08: b"\x1a\x08" + SERIAL,
            0x38: bytes.fromhex("1a1707"), 0x09: bytes.fromhex("1a0903"),
            0x0E: bytes.fromhex("1a0464"), 0x63: bytes.fromhex("1a3b0004480000"),
            0x12: bytes.fromhex("1a0598"), 0x11: bytes.fromhex("1a0689"),
            0x2F: bytes.fromhex("1a1d00"), 0x46: bytes.fromhex("1a2000"),
        }

    async def open(self):
        selection = _BleWriteEndpointResolver().resolve(self.services, required_write_char_uuid=FF02)
        self.io.apply_write_selection(selection)
        self.io.configure_endpoints(self.services)
        await self.io.start_notify_if_available(
            self, lambda source, data: self.io.handle_notification(data, source),
        )
        await self.io.initialize_connection(self, mtu_size=512, timeout=.02)
        return self

    async def start_notify(self, uuid, callback):
        uuid = getattr(uuid, "uuid", uuid)
        self.callbacks[uuid] = callback
        if self.grant:
            self.receive(self.grant + bytes((2, self.limit)))

    async def stop_notify(self, uuid):
        uuid = getattr(uuid, "uuid", uuid)
        self.callbacks.pop(uuid, None)

    def receive(self, data):
        parts = [bytes((byte,)) for byte in data] if self.fragment else [data]
        for part in parts:
            self.callbacks["reply"]("reply", part)

    async def write_gatt_char(self, char, data, response):
        await self.host_ready.wait()
        if self.fail_write:
            raise OSError("native write failed")
        self.calls.append(bytes(data))
        if self.replenish:
            self.receive(b"\x01\x01")
        if data.startswith(b"\x1f") or self.command_buffer:
            self.command_buffer.extend(data)
            while len(self.command_buffer) >= 3 and self.command_buffer[:2] == b"\x1f\x11":
                command = self.command_buffer[2]
                del self.command_buffer[:3]
                self.commands.append(command)
                reply = self.replies.get(command)
                if reply:
                    self.receive(reply)
        elif self.pages:
            self.page_data.extend(data)
            if self.page_data == self.pages[0]:
                self.pages.pop(0)
                self.page_data.clear()
                self.receive(bytes.fromhex("1a0f0c"))

    def __getattr__(self, name):
        return getattr(self.io, name)

    def can_wait_for_reply(self):
        return self.io.can_wait_for_notification()

    async def wait_for_reply(self, label, match, *, timeout, required):
        return await self.io.wait_for_notification(label, match, timeout=timeout, required=required)

    async def attach_runtime_controller(self, controller, *, timeout):
        await self.io.attach_runtime_controller(controller, mtu_size=512, timeout=timeout)

    async def send_standard_payload(self, data):
        await self.io.send(self, data, mtu_size=512, timeout=.02)

    async def send(self, job):
        await self.send_standard_payload(job.payload)

    async def disconnect(self):
        await self.io.stop_notify_if_started(self)
        self.is_connected = False

    async def connect(self, device):
        return await self.open()


class PhomemoBleFramingTests(unittest.TestCase):
    def test_every_split_preserves_status_data_and_coalesced_control_records(self):
        serial = b"\x01\x02\xaa\xab\xac\x01" + b"123456789"
        wire = (b"\x01\xff\x02\x40\x1a\x08" + serial
                + bytes.fromhex("aaabac010102 1a3601 1a3b0004480000 1a0f0c"))
        expected = [b"\x01\xff", b"\x02\x40", b"\x08" + serial,
                    bytes.fromhex("aaabac010102"), bytes.fromhex("3601"),
                    bytes.fromhex("3b0004480000"), bytes.fromhex("0f0c")]
        for split in range(len(wire) + 1):
            decoder = PhomemoBleReplyDecoder()
            self.assertEqual(decoder.feed(wire[:split]) + decoder.feed(wire[split:]), expected)
            self.assertFalse(decoder.has_pending_frame)
        decoder = PhomemoBleReplyDecoder()
        self.assertEqual([frame for byte in wire for frame in decoder.feed(bytes((byte,)))], expected)


class PhomemoBleSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_initializing_a_non_ble_connection_does_not_enable_write_credits(self):
        from tests.test_runtime_phomemo import ReplyConnection
        from timiniprint.printing.runtime.session import RuntimeConnectionSession

        link = ReplyConnection()
        session = RuntimeConnectionSession(link, reporter=reporting.DUMMY_REPORTER)
        controller = PhomemoRuntimeController(query_extended_info=False)
        await controller.initialize_connection(session, mtu_size=23, timeout=.01)
        self.assertFalse(controller.debug_snapshot()["ble_write_control"])
        self.assertIsNone(await controller.before_write(session, size=4096, timeout=.01))

    async def link(self, **kwargs):
        link = await BleConnection(**kwargs).open()
        self.addAsyncCleanup(link.disconnect)
        controller = PhomemoRuntimeController()
        controller.BLE_INITIAL_CREDIT_TIMEOUT_SEC = .04
        controller.BLE_CREDIT_TIMEOUT_SEC = .04
        await link.attach_runtime_controller(controller, timeout=.02)
        return link, controller

    async def test_public_connect_batches_metadata_and_keeps_the_existing_recipe(self):
        device = PrinterCatalog.load().device_from_model("phomemo_m02")
        for fragment in (False, True):
            link = BleConnection(fragment=fragment, pacing=20)
            controller = PhomemoRuntimeController(require_serial=True)
            async with await connect_printer(device, link, controller=controller, timeout=.1) as printer:
                self.assertEqual(printer.printer_device().protocol_variant, device.protocol_variant)
                self.assertEqual(printer.printer_device().profile, device.profile)
                self.assertEqual(printer.print_capabilities().serial_number, SERIAL)
                self.assertEqual(link.commands, [0x7B, 0x20, 0x45, 0x0E, 0x07, 0x5E,
                                                0x08, 0x09, 0x38, 0x8B, 0x63, 0x12, 0x11])
            self.assertFalse(link.callbacks)

    async def test_ordinary_bootstrap_has_no_first_command_exemption(self):
        link, controller = await self.link(grant=b"")
        link.replenish = False
        task = asyncio.create_task(link.send_standard_payload(b"metadata"))
        await asyncio.sleep(0)
        link.receive(bytes.fromhex("020a 0100"))
        await asyncio.sleep(.001)
        self.assertFalse(link.calls)
        link.receive(b"\x01\x01")
        await asyncio.wait_for(task, .2)
        self.assertEqual(link.calls, [b"metadata"])
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 0)

    async def test_initial_grants_received_before_runtime_attachment_are_replayed_once(self):
        link, controller = await self.link(grant=b"\x01\x03")
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 3)
        await link.attach_runtime_controller(controller, timeout=.02)
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 3)

    async def test_standard_gatt_metadata_cannot_manufacture_printer_credits(self):
        for service_uuid in ("180f", "vendor-service"):
            link = BleConnection(grant=b"")
            battery = SimpleNamespace(uuid="00002a19-0000-1000-8000-00805f9b34fb",
                                      service_uuid=service_uuid, properties=["notify"])
            link.services.append(SimpleNamespace(uuid=service_uuid, characteristics=[battery]))
            await link.open()
            controller = PhomemoRuntimeController()
            try:
                # Two independent battery updates must not become a split
                # printer grant. The same UUID in a vendor service is not
                # automatically classified as standard battery metadata.
                callback = link.callbacks[battery.uuid]
                callback(battery, b"\x01")
                callback(battery, b"\x32")
                await link.attach_runtime_controller(controller, timeout=.02)
                self.assertEqual(controller.debug_snapshot()["ble_write_credits"],
                                 0 if service_uuid == "180f" else 50)
            finally:
                await link.disconnect()

    async def test_startup_replay_outlives_recent_history_and_keeps_channel_fragments(self):
        link = await BleConnection(grant=b"\x01\x03").open()
        self.addAsyncCleanup(link.disconnect)
        for byte in b"\x1a\x08" + SERIAL:
            link.io.handle_notification(bytes((byte,)), source="metadata")
        for _ in range(20):
            link.io.handle_notification(b"\x01\x00", source="credits")
        controller = PhomemoRuntimeController(require_serial=True)
        with patch("timiniprint.transport.bluetooth.adapters.bleak_adapter_transport.time") as clock:
            clock.monotonic.return_value = 10**12
            await link.attach_runtime_controller(controller, timeout=.02)
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 3)
        link.replies.pop(0x08)
        from timiniprint.printing.runtime.session import RuntimeConnectionSession
        device = PrinterCatalog.load().device_from_model("phomemo_m02")
        prepared = await controller.prepare(device, RuntimeConnectionSession(link, reporter=reporting.DUMMY_REPORTER), timeout=.01)
        self.assertEqual(prepared.capabilities.serial_number, SERIAL)

    async def test_startup_replay_overflow_fails_instead_of_dropping_credits(self):
        link = await BleConnection().open()
        self.addAsyncCleanup(link.disconnect)
        link.io.handle_notification(b"x" * (64 * 1024 + 1))
        with self.assertRaisesRegex(RuntimeError, "startup notifications"):
            await link.attach_runtime_controller(PhomemoRuntimeController(), timeout=.02)
        self.assertFalse(link.calls)

    async def test_interleaved_credit_channel_cannot_become_serial_payload(self):
        link, controller = await self.link()
        link.io.handle_notification(b"\x1a\x08" + SERIAL[:5], source="metadata")
        link.io.handle_notification(bytes.fromhex("01ff"), source="credits")
        link.io.handle_notification(SERIAL[5:], source="metadata")
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 256)
        link.replies.pop(0x08)
        from timiniprint.printing.runtime.session import RuntimeConnectionSession
        prepared = await controller.prepare(
            PrinterCatalog.load().device_from_model("phomemo_m02"),
            RuntimeConnectionSession(link, reporter=reporting.DUMMY_REPORTER), timeout=.01,
        )
        self.assertEqual(prepared.capabilities.serial_number, SERIAL)

    async def test_default_limit_can_be_replaced_but_not_exceed_the_native_maximum(self):
        for announced, native, expected in ((250, 512, 250), (250, 80, 80), (None, 512, 182)):
            link, _ = await self.link(grant=b"")
            link.writer.max_write_without_response_size = native
            link.receive(b"\x01\x01" + (bytes((2, announced)) if announced else b""))
            await link.send_standard_payload(b"x" * 601)
            self.assertEqual(b"".join(link.calls), b"x" * 601)
            self.assertEqual(len(link.calls[0]), expected)
            self.assertTrue(all(len(chunk) <= expected for chunk in link.calls))

    async def test_credit_notification_from_another_thread_wakes_the_writer(self):
        link, controller = await self.link(grant=b"")
        task = asyncio.create_task(link.send_standard_payload(b"data"))
        await asyncio.sleep(0)
        await asyncio.to_thread(controller.handle_ble_notification, link.io, b"\x01\x01", source="credits")
        await asyncio.wait_for(task, .2)
        self.assertEqual(link.calls, [b"data"])

    async def test_public_preparation_without_initial_grant_times_out_and_closes(self):
        link = BleConnection(grant=b"", pacing=20)
        controller = PhomemoRuntimeController()
        controller.BLE_INITIAL_CREDIT_TIMEOUT_SEC = .02
        with self.assertRaisesRegex(TimeoutError, "write credit"):
            await connect_printer(PrinterCatalog.load().device_from_model("phomemo_m02"), link,
                                  controller=controller, timeout=.01)
        self.assertFalse(link.calls)
        self.assertFalse(link.callbacks)
        self.assertFalse(link.is_connected)

    async def test_payload_limit_is_rechecked_after_credit_wait_and_no_bytes_are_lost(self):
        link, _ = await self.link(grant=b"")
        task = asyncio.create_task(link.send_standard_payload(b"0123456789abcdef"))
        await asyncio.sleep(0)
        link.receive(bytes.fromhex("0203 0101"))
        await asyncio.wait_for(task, .2)
        self.assertEqual(link.calls, [b"012", b"345", b"678", b"9ab", b"cde", b"f"])

    async def test_unsigned_additive_grants_and_burst_counts(self):
        for selector, writes in ((0, 1), (1, 5), (3, 3)):
            link, controller = await self.link(grant=b"\x01\x01", limit=1)
            link.replenish = False
            link.receive(bytes((0x1A, 0x36, selector)))
            await link.send_standard_payload(b"x" * writes)
            self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 0)
            with self.assertRaisesRegex(TimeoutError, "write credit"):
                await link.send_standard_payload(b"y")
            link.receive(bytes.fromhex("01ff 0101"))
            self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 256)

    async def test_credit_counter_and_partial_burst_persist_between_sends(self):
        link, controller = await self.link(limit=1)
        link.replenish = False
        link.receive(bytes.fromhex("1a3601"))
        await link.send_standard_payload(b"12")
        await link.send_standard_payload(b"345")
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 0)
        self.assertEqual(controller.debug_snapshot()["ble_writes_in_credit"], 0)

    async def test_chip_serial_reset_is_conditional_delayed_and_not_an_initial_credit(self):
        link, controller = await self.link(grant=b"")
        link.receive(b"\x1a\x08" + SERIAL)
        self.assertFalse(controller.debug_snapshot()["ble_credit_reset_pending"])
        link.receive(b"\x1a\x36\x01\x1a\x08" + SERIAL)
        before = controller.debug_snapshot()
        self.assertTrue(before["ble_credit_reset_pending"])
        self.assertEqual(before["ble_write_credits"], 0)
        with patch("timiniprint.printing.runtime.phomemo_ble.time.monotonic", return_value=10**12):
            self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 1)

    async def test_diagnostic_counts_and_page_results_do_not_grant_write_credits(self):
        link, controller = await self.link(grant=b"")
        link.receive(bytes.fromhex("aaabac0101ff 1a0f0c"))
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 0)
        with self.assertRaisesRegex(TimeoutError, "write credit"):
            await link.send_standard_payload(b"raster")

    async def test_host_readiness_still_blocks_writing_with_printer_credits(self):
        link, _ = await self.link()
        link.host_ready.clear()
        task = asyncio.create_task(link.send_standard_payload(b"data"))
        await asyncio.sleep(.001)
        self.assertFalse(link.calls)
        link.host_ready.set()
        await asyncio.wait_for(task, .2)
        self.assertEqual(link.calls, [b"data"])

    async def test_failed_native_write_is_not_retried_or_refunded(self):
        link, controller = await self.link()
        link.fail_write = True
        with self.assertRaisesRegex(OSError, "native write failed"):
            await link.send_standard_payload(b"data")
        self.assertEqual(link.calls, [])
        self.assertEqual(controller.debug_snapshot()["ble_write_credits"], 0)

    async def test_cancellation_and_disconnect_release_credit_waits(self):
        for disconnect in (False, True):
            link, _ = await self.link(grant=b"")
            task = asyncio.create_task(link.send_standard_payload(b"data"))
            await asyncio.sleep(0)
            if disconnect:
                await link.disconnect()
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    await asyncio.wait_for(task, .2)
            else:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                link.receive(b"\x01\x01")
                await link.send_standard_payload(b"new")
                self.assertEqual(link.calls, [b"new"])

    async def test_zero_payload_limit_fails_before_native_write(self):
        link, _ = await self.link()
        link.receive(b"\x02\x00")
        with self.assertRaisesRegex(ValueError, "zero write-payload"):
            await link.send_standard_payload(b"data")
        self.assertFalse(link.calls)

    async def test_control_and_bulk_writes_share_the_window_and_respect_host_mtu(self):
        link, _ = await self.link(limit=7)
        self.assertTrue(await link.send_control_packet(b"a" * 30))
        self.assertEqual([len(chunk) for chunk in link.calls], [7, 7, 7, 7, 2])
        link.calls.clear()
        link.writer.max_write_without_response_size = 5
        link.io._transport_profile = replace(link.active_ble_profile, bulk_write=BleBulkWriteProfile("bulk", 100, 0))
        link.io.bindings.bulk_write_char = link.writer
        self.assertTrue(await link.io.send_bulk_payload(link, b"b" * 22, mtu_size=5, timeout=.02))
        self.assertEqual(b"".join(link.calls), b"b" * 22)
        self.assertTrue(all(len(chunk) <= 5 for chunk in link.calls))

    async def test_large_payload_and_step_jobs_wait_for_actual_page_results(self):
        from timiniprint.printing.runtime.base import PreparedPrinter
        from timiniprint.printing.send import send_prepared_job

        for step_based, buffered in ((False, False), (True, False), (True, True)):
            link, controller = await self.link(limit=40, fragment=True)
            device = PrinterCatalog.load().device_from_model("phomemo_m02")
            pages = [b"x" * 2048, b"y" * 1025] if step_based else [b"x" * 8192]
            link.pages = list(pages)
            steps = tuple(PhomemoPageStep(label="page", data=page, paginated=True, buffered=buffered)
                          for page in pages) if step_based else ()
            job = ProtocolJob(steps=steps, wait_for_completion=True) if step_based else ProtocolJob(
                payload=b"".join(pages), wait_for_completion=True,
            )
            if buffered:
                # Buffered pages arrive as one data buffer, but each has a result.
                link.pages = [b"".join(pages)]
                original_receive = link.receive
                def two_results(data):
                    original_receive(data + bytes.fromhex("1a0f0c") if data == bytes.fromhex("1a0f0c") else data)
                link.receive = two_results
            await send_prepared_job(PreparedPrinter(device, controller), link, job, timeout=.1)
            self.assertFalse(link.pages)
            raster = [chunk for chunk in link.calls if not chunk.startswith(b"\x1f")]
            self.assertEqual(b"".join(raster), b"".join(pages))

    async def test_debug_has_window_diagnostics_but_not_mac_identity(self):
        reporter, sink = build_capture_reporter()
        link, _ = await self.link(reporter=reporter)
        link.receive(b"\x1a\x0dAABBCCDDEEFF\x02\x40")
        text = "\n".join(message.detail or "" for message in sink.messages)
        self.assertIn("payload limit: 64", text)
        self.assertNotIn("41 41 42 42 43 43", text)
        self.assertNotIn("AABBCCDDEEFF", text)


class BleWriteSizeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_loss_during_the_last_write_cannot_report_a_successful_send(self):
        link = await BleConnection().open()
        self.addAsyncCleanup(link.disconnect)
        async def lost_during_write(*args, **kwargs):
            link.io.connection_lost()
        link.write_gatt_char = lost_during_write
        with self.assertRaisesRegex(ConnectionError, "lost"):
            await link.send_standard_payload(b"data")

    async def test_native_disconnect_wakes_credit_wait_without_waiting_for_its_deadline(self):
        from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket

        link = await BleConnection(grant=b"").open()
        self.addAsyncCleanup(link.disconnect)
        await link.attach_runtime_controller(PhomemoRuntimeController(), timeout=.02)
        socket = _BleakSocket()
        socket._client, socket._transport, socket._connected = link, link.io, True
        task = asyncio.create_task(link.send_standard_payload(b"data"))
        try:
            await asyncio.sleep(.001)
            socket._handle_disconnected(object())  # stale client cannot close this connection
            self.assertTrue(socket._connected)
            socket._handle_disconnected(link)
            self.assertFalse(socket._connected)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                await asyncio.wait_for(task, .2)
            self.assertFalse(link.calls)
            await link.disconnect()
            self.assertIsNone(link.io._disconnected_runtime_task)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_native_disconnect_fails_passive_reply_waits_and_paused_writes(self):
        link = await BleConnection().open()
        self.addAsyncCleanup(link.disconnect)
        await link.attach_runtime_controller(RuntimeController(), timeout=.02)
        link.io._transport_profile = replace(link.active_ble_profile, flow_controlled_standard_write=True)
        link.io.set_flow_paused(True)
        writer = asyncio.create_task(link.send_standard_payload(b"data"))
        reply = asyncio.create_task(link.wait_for_reply("reply", lambda data: False, timeout=30, required=True))
        try:
            await asyncio.sleep(.001)
            link.io.connection_lost()
            for task in (writer, reply):
                with self.assertRaisesRegex(ConnectionError, "lost"):
                    await asyncio.wait_for(task, .2)
            self.assertFalse(link.calls)
        finally:
            for task in (writer, reply):
                task.cancel()
            await asyncio.gather(writer, reply, return_exceptions=True)

    async def test_disconnected_runtime_stop_failure_remains_visible_and_retryable(self):
        class StopFailure(RuntimeController):
            fail = True
            async def stop(self, session):
                if self.fail:
                    raise ValueError("runtime stop failed")

        link = await BleConnection().open()
        controller = StopFailure()
        await link.attach_runtime_controller(controller, timeout=.02)
        link.io.connection_lost()
        try:
            with self.assertRaisesRegex(ValueError, "runtime stop failed"):
                await link.disconnect()
            self.assertIs(link.io._runtime_controller, controller)
        finally:
            controller.fail = False
            await link.disconnect()
        self.assertIsNone(link.io._disconnected_runtime_task)
        self.assertFalse(link.callbacks)

    async def test_old_page_result_during_first_credit_wait_cannot_complete_the_new_page(self):
        from timiniprint.printing.runtime.base import PreparedPrinter
        from timiniprint.printing.send import send_prepared_job

        for step_based in (False, True):
            link = await BleConnection(grant=b"").open()
            self.addAsyncCleanup(link.disconnect)
            controller = PhomemoRuntimeController()
            controller.COMPLETION_TIMEOUT_SEC = .04
            await link.attach_runtime_controller(controller, timeout=.02)
            link.replenish = False
            device = PrinterCatalog.load().device_from_model("phomemo_m02")
            job = (ProtocolJob(steps=(PhomemoPageStep(label="page", data=b"x"),), wait_for_completion=True)
                   if step_based else ProtocolJob(payload=b"x", wait_for_completion=True))
            task = asyncio.create_task(send_prepared_job(PreparedPrinter(device, controller), link, job, timeout=.02))
            try:
                await asyncio.sleep(.001)
                link.receive(bytes.fromhex("1a0f0c"))
                self.assertEqual(controller.debug_snapshot()["received_results"], 0)
                link.receive(b"\x01\x01")
                with self.assertRaisesRegex(RuntimeError, "completion timed out"):
                    await asyncio.wait_for(task, .3)
                self.assertEqual(link.calls, [b"x"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_fault_during_credit_wait_aborts_payload_and_step_jobs(self):
        from timiniprint.printing.errors import PrinterNotReadyError
        from timiniprint.printing.runtime.base import PreparedPrinter
        from timiniprint.printing.send import send_prepared_job

        for step_based in (False, True):
            link = await BleConnection(limit=10).open()
            self.addAsyncCleanup(link.disconnect)
            controller = PhomemoRuntimeController()
            controller.BLE_CREDIT_TIMEOUT_SEC = .1
            await link.attach_runtime_controller(controller, timeout=.02)
            link.replenish = False
            device = PrinterCatalog.load().device_from_model("phomemo_m02")
            job = (ProtocolJob(steps=(PhomemoPageStep(label="page", data=b"x" * 100),), wait_for_completion=True)
                   if step_based else ProtocolJob(payload=b"x" * 100, wait_for_completion=True))
            task = asyncio.create_task(send_prepared_job(PreparedPrinter(device, controller), link, job, timeout=.02))
            await asyncio.sleep(.001)
            link.receive(bytes.fromhex("1a0599"))
            with self.assertRaises(PrinterNotReadyError):
                await asyncio.wait_for(task, .2)
            self.assertEqual(link.calls, [b"x" * 10])

    async def test_invalid_runtime_limit_never_reaches_the_native_writer(self):
        for permitted in (0, -1, True, 1.5, 11):
            link = await BleConnection().open()
            self.addAsyncCleanup(link.disconnect)
            class InvalidSize(RuntimeController):
                async def before_write(self, session, *, size, timeout):
                    return permitted
            await link.attach_runtime_controller(InvalidSize(), timeout=.02)
            with self.assertRaisesRegex(ValueError, "Runtime write size"):
                await link.send_standard_payload(b"0123456789")
            self.assertFalse(link.calls)


class PhomemoBleProfileTests(unittest.IsolatedAsyncioTestCase):
    def test_required_writer_does_not_fall_back_to_another_uart(self):
        other = SimpleNamespace(uuid="0000ae01-0000-1000-8000-00805f9b34fb", properties=["write"])
        exact = SimpleNamespace(uuid=FF02, properties=["write-without-response"])
        services = [SimpleNamespace(uuid="vendor", characteristics=[other, exact])]
        resolver = _BleWriteEndpointResolver()
        self.assertIs(resolver.resolve(services, required_write_char_uuid=FF02).char, exact)
        services[0].characteristics = [other]
        self.assertIsNone(resolver.resolve(services, required_write_char_uuid=FF02))
        self.assertIsNone(resolver.resolve(services, required_write_char_uuid=FF02,
                                          gatt_layouts=(BleGattLayout("vendor", other.uuid, ""),)))

    def test_printmaster_keeps_its_previous_profile_and_phomemo_keeps_spp_preference(self):
        profile = get_ble_transport_profile(ProtocolFamily.PHOMEMO_ESC, protocol_variant="printmaster_m110")
        self.assertFalse(profile.notify_all_services)
        self.assertEqual(profile.required_write_char_uuid, "")
        self.assertEqual(profile.standard_chunk_cap, 128)
        self.assertEqual(profile.standard_write_delay_ms, 20)
        device = PrinterCatalog.load().device_from_model("phomemo_m02")
        self.assertTrue(device.profile.use_spp)
        self.assertEqual(device.ble_transport_profile.required_write_char_uuid, FF02)

    async def test_duplicate_notify_uuids_keep_instances_and_channel_identity(self):
        class Client:
            def __init__(self):
                self.callbacks = []
                self.stopped = []
            async def start_notify(self, target, callback):
                self.callbacks.append((target, callback))
            async def stop_notify(self, target):
                self.stopped.append(target)

        class Observer(RuntimeController):
            def __init__(self):
                self.sources = []
            def handle_ble_notification(self, session, payload, *, source):
                self.sources.append(source)

        reply1 = SimpleNamespace(uuid="same-reply", properties=["notify"], handle=11)
        reply2 = SimpleNamespace(uuid="same-reply", properties=["notify"], handle=22)
        services = [SimpleNamespace(uuid="a", characteristics=[reply1]),
                    SimpleNamespace(uuid="b", characteristics=[reply2])]
        session = _BleakTransportSession(get_ble_transport_profile(ProtocolFamily.PHOMEMO_ESC),
                                        _BleWriteEndpointResolver(), reporting.DUMMY_REPORTER)
        session.configure_endpoints(services)
        client, observer = Client(), Observer()
        await session.start_notify_if_available(client, lambda source, data: session.handle_notification(data, source))
        self.assertEqual([target for target, _ in client.callbacks], [reply1, reply2])
        # Replay must retain exactly the same IDs used for live reception.
        for target, callback in client.callbacks:
            callback(target, b"before")
        await session.attach_runtime_controller(observer, mtu_size=20, timeout=.02)
        before = list(observer.sources)
        for target, callback in client.callbacks:
            callback(target, b"after")
        self.assertEqual(len(set(before)), 2)
        self.assertEqual(observer.sources, before + before)
        await session.stop_notify_if_started(client)
        self.assertEqual(client.stopped, [reply1, reply2])
