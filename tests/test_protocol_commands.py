from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub
from timiniprint.protocol.family import ProtocolCommandSet, ProtocolFamily, ProtocolTransportStyle


class ProtocolCommandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.commands = importlib.import_module("timiniprint.protocol.commands")

    def test_make_packet_headers_by_protocol_family(self) -> None:
        payload = b"\x01\x02\x03"
        packet_tiny = self.commands.make_packet(0xA2, payload, ProtocolFamily.TINY)
        packet_prefixed = self.commands.make_packet(0xA2, payload, ProtocolFamily.TINY_PREFIXED)
        packet_v5x = self.commands.make_packet(0xA2, payload, ProtocolFamily.V5X)
        packet_v5c = self.commands.make_packet(0xA2, payload, ProtocolFamily.V5C)
        packet_dck = self.commands.make_packet(0xA2, payload, ProtocolFamily.DCK)

        self.assertTrue(packet_tiny.startswith(bytes([0x51, 0x78, 0xA2, 0x00, 0x03, 0x00])))
        self.assertTrue(packet_prefixed.startswith(bytes([0x12, 0x51, 0x78, 0xA2, 0x00, 0x03, 0x00])))
        self.assertTrue(packet_v5x.startswith(bytes([0x22, 0x21, 0xA2, 0x00, 0x03, 0x00])))
        self.assertTrue(packet_v5c.startswith(bytes([0x56, 0x88, 0xA2, 0x00, 0x03, 0x00])))
        self.assertTrue(packet_dck.startswith(bytes([0x55, 0xAA, 0xA2, 0x00, 0x03, 0x00])))
        self.assertEqual(packet_tiny[-1], 0xFF)

    def test_protocol_specs_expose_command_set_and_transport_style(self) -> None:
        self.assertEqual(ProtocolFamily.TINY.command_set, ProtocolCommandSet.TINY)
        self.assertEqual(ProtocolFamily.TINY_PREFIXED.command_set, ProtocolCommandSet.TINY)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL.command_set, ProtocolCommandSet.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL_A4.command_set, ProtocolCommandSet.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.V5X.command_set, ProtocolCommandSet.V5X)
        self.assertEqual(ProtocolFamily.V5C.command_set, ProtocolCommandSet.V5C)
        self.assertEqual(ProtocolFamily.DCK.command_set, ProtocolCommandSet.DCK)
        self.assertEqual(ProtocolFamily.ELEPH_HPRT_ESC.command_set, ProtocolCommandSet.ELEPH_HPRT_ESC)
        self.assertEqual(ProtocolFamily.ELEPH_TSPL.command_set, ProtocolCommandSet.ELEPH_TSPL)
        self.assertEqual(ProtocolFamily.PHOMEMO_ESC.command_set, ProtocolCommandSet.PHOMEMO_ESC)

        self.assertEqual(ProtocolFamily.TINY.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.TINY_PREFIXED.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL_A4.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.V5X.transport_style, ProtocolTransportStyle.SPLIT_BULK)
        self.assertEqual(ProtocolFamily.V5C.transport_style, ProtocolTransportStyle.FLOW_CONTROLLED)
        self.assertEqual(ProtocolFamily.DCK.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.ELEPH_HPRT_ESC.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.ELEPH_TSPL.transport_style, ProtocolTransportStyle.STANDARD)
        self.assertEqual(ProtocolFamily.PHOMEMO_ESC.transport_style, ProtocolTransportStyle.STANDARD)

    def test_protocol_family_accepts_current_serialized_values(self) -> None:
        self.assertEqual(ProtocolFamily.from_value(None), ProtocolFamily.TINY)
        self.assertEqual(ProtocolFamily.from_value("tiny"), ProtocolFamily.TINY)
        self.assertEqual(ProtocolFamily.from_value("tiny_prefixed"), ProtocolFamily.TINY_PREFIXED)
        self.assertEqual(ProtocolFamily.from_value("luck_normal"), ProtocolFamily.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.from_value("luck_normal_a4"), ProtocolFamily.LUCK_NORMAL_A4)
        self.assertEqual(ProtocolFamily.from_value("v5x"), ProtocolFamily.V5X)
        self.assertEqual(ProtocolFamily.from_value("v5c"), ProtocolFamily.V5C)
        self.assertEqual(ProtocolFamily.from_value("dck"), ProtocolFamily.DCK)
        self.assertEqual(ProtocolFamily.from_value("eleph_hprt_esc"), ProtocolFamily.ELEPH_HPRT_ESC)
        self.assertEqual(ProtocolFamily.from_value("eleph_tspl"), ProtocolFamily.ELEPH_TSPL)
        self.assertEqual(ProtocolFamily.from_value("phomemo_esc"), ProtocolFamily.PHOMEMO_ESC)

    def test_luck_normal_families_do_not_expose_prefixed_packet_layout(self) -> None:
        self.assertFalse(ProtocolFamily.LUCK_NORMAL.uses_prefixed_packets)
        self.assertFalse(ProtocolFamily.LUCK_NORMAL_A4.uses_prefixed_packets)
        with self.assertRaisesRegex(ValueError, "does not use prefixed command packets"):
            self.commands.make_packet(0xA2, b"\x01", ProtocolFamily.LUCK_NORMAL)

    def test_blackening_cmd_clamps_range(self) -> None:
        low = self.commands.blackening_cmd(0, ProtocolFamily.TINY)
        high = self.commands.blackening_cmd(99, ProtocolFamily.TINY)
        self.assertIn(bytes([0x31]), low)
        self.assertIn(bytes([0x35]), high)

    def test_energy_cmd_empty_for_non_positive(self) -> None:
        self.assertEqual(self.commands.energy_cmd(0, ProtocolFamily.TINY), b"")
        self.assertEqual(self.commands.energy_cmd(-1, ProtocolFamily.TINY_PREFIXED), b"")

    def test_paper_payload_for_dpi_300_and_default(self) -> None:
        cmd_300 = self.commands.paper_cmd(300, ProtocolFamily.TINY)
        cmd_203 = self.commands.paper_cmd(203, ProtocolFamily.TINY)
        self.assertIn(bytes([0x48, 0x00]), cmd_300)
        self.assertIn(bytes([0x30, 0x00]), cmd_203)

    def test_basic_command_ids(self) -> None:
        self.assertEqual(self.commands.print_mode_cmd(True, ProtocolFamily.TINY)[2], 0xBE)
        self.assertEqual(self.commands.feed_paper_cmd(7, ProtocolFamily.TINY)[2], 0xBD)
        self.assertEqual(self.commands.dev_state_cmd(ProtocolFamily.TINY)[2], 0xA3)
        self.assertEqual(self.commands.advance_paper_cmd(203, ProtocolFamily.TINY)[2], 0xA1)
        self.assertEqual(self.commands.retract_paper_cmd(203, ProtocolFamily.TINY)[2], 0xA0)

    def test_v5x_manual_motion_uses_family_override(self) -> None:
        feed = self.commands.advance_paper_cmd(203, ProtocolFamily.V5X)
        retract = self.commands.retract_paper_cmd(203, ProtocolFamily.V5X)

        self.assertTrue(feed.startswith(bytes([0x22, 0x21, 0xA3, 0x00, 0x02, 0x00])))
        self.assertIn(bytes([0x05, 0x00]), feed)
        self.assertTrue(retract.startswith(bytes([0x22, 0x21, 0xA4, 0x00, 0x02, 0x00])))
        self.assertIn(bytes([0x05, 0x00]), retract)

    def test_luck_normal_manual_motion_uses_plain_line_feed_commands(self) -> None:
        feed = self.commands.advance_paper_cmd(203, ProtocolFamily.LUCK_NORMAL)
        retract = self.commands.retract_paper_cmd(203, ProtocolFamily.LUCK_NORMAL)
        a4_feed = self.commands.advance_paper_cmd(203, ProtocolFamily.LUCK_NORMAL_A4)

        self.assertEqual(feed, bytes([0x1B, 0x4A, 0x50]))
        self.assertEqual(retract, bytes([0x1F, 0x11, 0x11, 0x50]))
        self.assertEqual(a4_feed, bytes([0x1B, 0x4A, 0x90]))

    def test_luck_normal_manual_motion_accepts_variant_overrides(self) -> None:
        qirui_q2_feed = self.commands.advance_paper_cmd(
            300,
            ProtocolFamily.LUCK_NORMAL,
            "qirui_q2",
        )
        qirui_q2_retract = self.commands.retract_paper_cmd(
            300,
            ProtocolFamily.LUCK_NORMAL,
            "qirui_q2",
        )

        self.assertEqual(qirui_q2_feed, bytes([0x1B, 0x4A, 0x82]))
        self.assertEqual(qirui_q2_retract, bytes([0x1F, 0x11, 0x11, 0x82]))


if __name__ == "__main__":
    unittest.main()
