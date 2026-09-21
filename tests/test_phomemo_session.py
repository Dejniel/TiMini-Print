from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_runtime_phomemo import ReplyConnection
from timiniprint import reporting
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.phomemo import PhomemoRuntimeController
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, PrinterStatusCode
from timiniprint.protocol.families.phomemo_esc.flow import PhomemoCapabilities


STARTUP_QUERIES = [bytes((0x1F, 0x11, op)) for op in
                   (0x38, 0x07, 0x09, 0x08, 0x0E, 0x63, 0x5E, 0x56, 0x51, 0x12, 0x11)]
SERIAL = b"Q17112345678901"


class PhomemoSessionTests(unittest.IsolatedAsyncioTestCase):
    def device(self, name="M02"):
        return replace(PrinterCatalog.load().device_from_model("phomemo_m02"), display_name=name)

    async def prepare(self, connection, *, name="M02", controller=None, timeout=1.0):
        with patch("timiniprint.printing.runtime.phomemo.asyncio.sleep", new_callable=AsyncMock) as sleep:
            if controller is None:
                context = await prepare_connection_runtime(self.device(name), connection, timeout=timeout)
            else:
                session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
                await session.attach_runtime_controller(controller, timeout=timeout)
                context = await controller.prepare(self.device(name), session, timeout=timeout)
        return context, sleep

    async def test_startup_order_decodes_metadata_and_publishes_immutable_capabilities(self):
        class FragmentedConnection(ReplyConnection):
            def deliver(self, data):
                for byte in data:
                    super().deliver(bytes((byte,)))

        class UnobservedConnection(ReplyConnection):
            attach_runtime_controller = None

        for cls in (ReplyConnection, FragmentedConnection, UnobservedConnection):
            with self.subTest(connection=cls.__name__):
                connection = cls()
                connection.query_replies.update({
                    bytes.fromhex("1f1138"): bytes.fromhex("1a1707"),
                    # Unsolicited features/charging can accompany any solicited reply.
                    bytes.fromhex("1f1107"): bytes.fromhex("1a0701fe03 1a3b00045a0d00 1a3502"),
                    bytes.fromhex("1f1109"): bytes.fromhex("1a0903"),
                    bytes.fromhex("1f1108"): b"\x1a\x08" + SERIAL,
                    bytes.fromhex("1f110e"): bytes.fromhex("1a04a2"),
                })
                context, sleep = await self.prepare(connection)
                self.assertEqual(connection.queries, STARTUP_QUERIES)
                self.assertEqual([call.args for call in sleep.await_args_list], [(0.1,)] * len(STARTUP_QUERIES))
                self.assertEqual(context.device, self.device())
                self.assertEqual(context.capabilities, PhomemoCapabilities(
                    serial_number=SERIAL, chip_type=7, reported_supports_gray=True,
                    reported_gray_levels=32, double_dpi=True, charging_print_restricted=True,
                    multiple_densities=True, label_workshop=True, paper_sensor_suppression=True,
                ))
                self.assertIsNone(context.capabilities.supports_gray)
                self.assertIsNone(context.capabilities.gray_level_override)
                snapshot = context.runtime_controller.debug_snapshot()
                for key, expected in {"firmware": "1.-2.3", "shutdown_value": 15, "battery": 5,
                                      "battery_low": True, "charging": True, "serial_prefix": "Q171"}.items():
                    self.assertEqual(snapshot[key], expected)
                with self.assertRaises(FrozenInstanceError):
                    context.capabilities.serial_number = b"changed"
                context.runtime_controller.handle_notification(connection.session, bytes.fromhex("1a3b0000000000"))
                self.assertEqual(context.capabilities.reported_gray_levels, 32)
                self.assertEqual(context.runtime_controller.debug_snapshot()["reported_gray_levels"], 16)
                self.assertFalse(context.runtime_controller.debug_snapshot()["reported_supports_gray"])

    async def test_missing_optional_metadata_does_not_block_a_print(self):
        connection = ReplyConnection([[bytes.fromhex("1a0f0c")]])
        context, _ = await self.prepare(connection)
        self.assertEqual(context.capabilities, PhomemoCapabilities())
        await send_prepared_job(context, connection, ProtocolJob(payload=b"raster", wait_for_completion=True))
        self.assertEqual(connection.sent, [b"raster"])

    async def test_extended_info_can_be_omitted_without_skipping_identity_and_readiness(self):
        connection = ReplyConnection()
        connection.query_replies[b"\x1f\x11\x08"] = b"\x1a\x08" + SERIAL
        context, sleep = await self.prepare(
            connection, controller=PhomemoRuntimeController(query_extended_info=False),
        )
        expected = [bytes((0x1F, 0x11, opcode)) for opcode in (0x38, 0x07, 0x09, 0x08, 0x0E, 0x12, 0x11)]
        self.assertEqual(connection.queries, expected)
        self.assertEqual(context.capabilities.serial_number, SERIAL)
        self.assertEqual(sleep.await_count, len(expected))

    async def test_optional_metadata_waits_are_bounded_but_required_serial_uses_caller_timeout(self):
        class TimedConnection(ReplyConnection):
            async def query_control_packet(self, data, *, timeout, reply_complete):
                timeouts.append(timeout)
                return await super().query_control_packet(data, timeout=timeout, reply_complete=reply_complete)

        for timeout in (0.1, 3.0):
            timeouts = []
            connection = TimedConnection()
            connection.query_replies[b"\x1f\x11\x08"] = b"\x1a\x08" + SERIAL
            await self.prepare(connection, controller=PhomemoRuntimeController(require_serial=True), timeout=timeout)
            expected = [min(timeout, 0.5)] * 9 + [timeout] * 2
            expected[3] = timeout
            self.assertEqual(timeouts, expected)

    async def test_required_serial_rejects_missing_truncated_and_bare_responses(self):
        for reply in (None, SERIAL, b"\x1a\x08" + SERIAL[:4]):
            with self.subTest(reply=reply):
                connection = ReplyConnection()
                connection.query_replies[b"\x1f\x11\x08"] = reply
                with self.assertRaises(RuntimeError):
                    await self.prepare(connection, controller=PhomemoRuntimeController(require_serial=True))
                self.assertFalse(connection.sent)

    async def test_serial_from_previous_connection_cannot_satisfy_required_query(self):
        first = ReplyConnection()
        first.query_replies[b"\x1f\x11\x08"] = b"\x1a\x08" + SERIAL
        previous, _ = await self.prepare(first, controller=PhomemoRuntimeController(require_serial=True))
        with self.assertRaises(RuntimeError):
            await self.prepare(ReplyConnection(), controller=PhomemoRuntimeController(require_serial=True))
        self.assertEqual(previous.capabilities.serial_number, SERIAL)

    async def test_atomic_ble_queries_share_metadata_decoder(self):
        class NotificationConnection(ReplyConnection):
            def can_query_control_packet(self):
                return False

            def can_send_control_packet_wait_notification(self):
                return True

            async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
                self.queries.append(packet)
                for byte in self.query_replies.get(packet, b""):
                    part = bytes((byte,))
                    self.deliver(part)
                    if match(part):
                        return part
                return None

        class UnobservedNotificationConnection(NotificationConnection):
            attach_runtime_controller = None

        for cls in (NotificationConnection, UnobservedNotificationConnection):
            connection = cls()
            connection.query_replies[b"\x1f\x11\x08"] = b"\x1a\x08" + SERIAL
            context, _ = await self.prepare(connection, controller=PhomemoRuntimeController(require_serial=True))
            self.assertEqual(connection.queries, STARTUP_QUERIES)
            self.assertEqual(context.capabilities.serial_number, SERIAL)

    async def test_exact_m02h_reset_and_t02_legacy_chip_reply(self):
        for name in ("M02H", "M02PRE", "M02H_123", "m02h", "T02", "T02_123", "t02", "M02"):
            with self.subTest(name=name):
                connection = ReplyConnection()
                connection.query_replies[b"\x1f\x11\x38"] = bytes.fromhex("1a1603")
                context, _ = await self.prepare(connection, name=name)
                self.assertEqual(connection.sent, [b"\x1b\x40"] if name == "M02H" else [])
                self.assertEqual(context.capabilities.chip_type, 3 if name == "T02" else None)

    async def test_extended_queries_accept_known_prefixed_frames_only(self):
        connection = ReplyConnection()
        context, _ = await self.prepare(connection)
        for reply, expected in ((bytes.fromhex("3502"), None),
                                (bytes.fromhex("1a99"), None),
                                (bytes.fromhex("1a3502"), b"\x02")):
            connection.query_replies[b"\x1f\x11\x63"] = reply
            with patch("timiniprint.printing.runtime.phomemo.asyncio.sleep", new_callable=AsyncMock):
                result = await context.runtime_controller.query_reply(
                    connection.session, b"\x1f\x11\x63", None, timeout=0.1, required=False,
                )
            self.assertEqual(result, expected)

    async def test_battery_charging_and_shutdown_decode_without_affecting_print_faults(self):
        connection = ReplyConnection()
        context, _ = await self.prepare(connection)
        controller = context.runtime_controller
        for wire, value, low in ((0xA1, 10, True), (0xA2, 5, True), (0xA3, 3, True),
                                 (100, 100, False), (0xFF, -1, False)):
            controller.handle_notification(connection.session, bytes((0x1A, 0x04, wire)))
            self.assertEqual(controller.debug_snapshot()["battery"], value)
            self.assertEqual(controller.debug_snapshot()["battery_low"], low)
        controller.handle_notification(connection.session, bytes.fromhex("1a09ff 1a3500"))
        self.assertEqual(controller.debug_snapshot()["shutdown_value"], -5)
        self.assertFalse(controller.debug_snapshot()["charging"])
        self.assertIsNone(controller.debug_snapshot()["reported_supports_gray"])

    async def test_transport_failure_is_not_hidden_as_optional_metadata_timeout(self):
        class BrokenConnection(ReplyConnection):
            async def query_control_packet(self, data, *, timeout, reply_complete):
                raise OSError("connection lost")

        with self.assertRaisesRegex(OSError, "connection lost"):
            await self.prepare(BrokenConnection())

    async def test_required_chip_query_uses_chip_reply_not_an_unrelated_status(self):
        for reply in (None, bytes.fromhex("1a0598"), bytes.fromhex("1a1700"), bytes.fromhex("1a1707")):
            connection = ReplyConnection()
            connection.query_replies[b"\x1f\x11\x38"] = reply
            controller = PhomemoRuntimeController(require_chip=True)
            if reply is None or reply[1] != 0x17:
                with self.assertRaises(RuntimeError):
                    await self.prepare(connection, controller=controller)
            else:
                context, _ = await self.prepare(connection, controller=controller)
                self.assertEqual(context.capabilities.chip_type, reply[-1])

    async def test_feature_bits_are_independent_and_gray_exponent_zero_means_sixteen(self):
        connection = ReplyConnection()
        context, _ = await self.prepare(connection)
        fields = ("reported_supports_gray", "double_dpi", "charging_print_restricted",
                  "multiple_densities", "label_workshop", "paper_sensor_suppression")
        for bits, selected in (((4, 0, 0), fields[0]), ((0, 8, 0), fields[1]),
                               ((0, 2, 0), fields[2]), ((0, 0, 1), fields[3]),
                               ((0, 0, 8), fields[4]), ((0, 0, 4), fields[5]),
                               ((0, 0, 0), None)):
            context.runtime_controller.handle_notification(connection.session, bytes((0x1A, 0x3B, 0, *bits, 0)))
            snapshot = context.runtime_controller.debug_snapshot()
            self.assertEqual({field: snapshot[field] for field in fields},
                             {field: field == selected for field in fields})
            self.assertEqual(snapshot["reported_gray_levels"], 16)
        for exponent in (1, 2, 4, 5, 15):
            context.runtime_controller.handle_notification(
                connection.session, bytes((0x1A, 0x3B, 0, 4, exponent << 4, 0, 0)),
            )
            self.assertEqual(context.runtime_controller.debug_snapshot()["reported_gray_levels"], 1 << exponent)

    async def test_write_only_connection_can_prepare_unless_serial_is_required(self):
        class WriteOnlyConnection:
            async def send_standard_payload(self, data):
                pass

        context, _ = await self.prepare(WriteOnlyConnection())
        self.assertEqual(context.capabilities, PhomemoCapabilities())
        with self.assertRaisesRegex(RuntimeError, "15-byte serial"):
            await self.prepare(WriteOnlyConnection(), controller=PhomemoRuntimeController(require_serial=True))

    async def test_fault_received_with_metadata_prevents_raster_send(self):
        connection = ReplyConnection()
        connection.query_replies[b"\x1f\x11\x07"] = bytes.fromhex("1a07010203 1a0599")
        connection.query_replies[b"\x1f\x11\x12"] = None
        context, _ = await self.prepare(connection)
        with self.assertRaises(PrinterNotReadyError) as error:
            await send_prepared_job(context, connection, ProtocolJob(payload=b"raster", wait_for_completion=True))
        self.assertEqual(error.exception.reasons, (PrinterStatusCode.COVER_OPEN,))
        self.assertFalse(connection.sent)
