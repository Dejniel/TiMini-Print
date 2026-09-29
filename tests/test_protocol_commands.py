from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub
from timiniprint.protocol.family import ProtocolCommandSet, ProtocolFamily


class ProtocolCommandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.commands = importlib.import_module("timiniprint.protocol.commands")
        cls.packet = importlib.import_module("timiniprint.protocol.packet")

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

    def test_prefixed_packet_codec_reads_and_splits_complete_frames(self) -> None:
        first = self.packet.make_packet(0xA2, b"\x01\x02", ProtocolFamily.V5X)
        second = self.packet.make_packet(0xA3, b"\x03", ProtocolFamily.V5X)

        self.assertEqual(
            self.packet.split_prefixed_packets(first + second, ProtocolFamily.V5X),
            [first, second],
        )
        self.assertEqual(
            self.packet.prefixed_packet_opcode(first, ProtocolFamily.V5X),
            0xA2,
        )
        self.assertEqual(
            self.packet.prefixed_packet_payload(first, ProtocolFamily.V5X),
            b"\x01\x02",
        )
        self.assertIsNone(
            self.packet.split_prefixed_packets(first + b"\x00", ProtocolFamily.V5X)
        )

    def test_prefixed_packet_stream_decoder_handles_fragmented_and_coalesced_frames(self) -> None:
        pause = bytes.fromhex("5178AE0101001070FF")
        resume = bytes.fromhex("5178AE0101000000FF")
        decoder = self.packet.PrefixedPacketStreamDecoder(ProtocolFamily.TINY)

        self.assertEqual(decoder.feed(b"\x00\x51"), ())
        packets = decoder.feed(pause[1:] + resume)

        self.assertEqual([packet.raw for packet in packets], [pause, resume])
        self.assertEqual(
            [(packet.opcode, packet.flags, packet.payload) for packet in packets],
            [(0xAE, 1, b"\x10"), (0xAE, 1, b"\x00")],
        )
        self.assertEqual(decoder.pending, b"")

    def test_prefixed_payload_does_not_require_a_frame_trailer(self) -> None:
        for family in (
            ProtocolFamily.TINY,
            ProtocolFamily.TINY_PREFIXED,
            ProtocolFamily.V5X,
            ProtocolFamily.V5C,
            ProtocolFamily.V5G,
        ):
            for payload in (b"", b"\x00\xFF", bytes(range(256)) + b"\x23"):
                packet = self.packet.make_packet(0xA9, payload, family)
                for trailer_length in (0, 1, 2):
                    with self.subTest(family=family, payload_length=len(payload), trailer_length=trailer_length):
                        self.assertEqual(
                            self.packet.prefixed_packet_payload(packet[:len(packet) - 2 + trailer_length], family),
                            payload,
                        )

    def test_prefixed_payload_rejects_incomplete_header_or_payload(self) -> None:
        for family in (ProtocolFamily.TINY, ProtocolFamily.TINY_PREFIXED, ProtocolFamily.V5X, ProtocolFamily.V5C):
            packet = self.packet.make_packet(0xA9, b"\x00\x23", family)
            for length in range(len(packet) - 2):
                with self.subTest(family=family, length=length):
                    self.assertIsNone(self.packet.prefixed_packet_payload(packet[:length], family))
            wrong_prefix = b"\x00" + packet[1:]
            self.assertIsNone(self.packet.prefixed_packet_payload(wrong_prefix, family))
        self.assertIsNone(self.packet.prefixed_packet_payload(packet, ProtocolFamily.LUCK_NORMAL))

    def test_prefixed_payload_reads_compact_v5x_notifications(self) -> None:
        for packet_hex, expected in (
            ("2221a90001000000", b"\x00"),
            ("2221b1000900312e392e332e312e3200", b"1.9.3.1.2"),
        ):
            with self.subTest(packet=packet_hex):
                self.assertEqual(
                    self.packet.prefixed_packet_payload(bytes.fromhex(packet_hex), ProtocolFamily.V5X),
                    expected,
                )

    def test_prefixed_payload_extraction_preserves_frame_boundaries(self) -> None:
        packet = self.packet.make_packet(0xA9, b"\x00", ProtocolFamily.V5X)
        without_trailer = packet[:-2]
        self.assertEqual(self.packet.prefixed_packet_payload(without_trailer, ProtocolFamily.V5X), b"\x00")
        self.assertIsNone(self.packet.prefixed_packet_length(without_trailer, 0, ProtocolFamily.V5X))
        self.assertIsNone(self.packet.split_prefixed_packets(without_trailer, ProtocolFamily.V5X))
        decoder = self.packet.PrefixedPacketStreamDecoder(ProtocolFamily.V5X)
        self.assertEqual(decoder.feed(without_trailer), ())
        self.assertEqual(decoder.pending, without_trailer)
        self.assertEqual([frame.raw for frame in decoder.feed(packet[-2:])], [packet])

    def test_prefixed_packet_stream_decoder_resynchronizes_after_invalid_crc(self) -> None:
        pause = bytes.fromhex("5178AE0101001070FF")
        resume = bytes.fromhex("5178AE0101000000FF")
        invalid = pause[:-2] + b"\x00\xFF"
        decoder = self.packet.PrefixedPacketStreamDecoder(ProtocolFamily.TINY)

        packets = decoder.feed(invalid + resume)

        self.assertEqual([packet.raw for packet in packets], [resume])

    def test_protocol_specs_expose_command_set(self) -> None:
        self.assertEqual(ProtocolFamily.TINY.command_set, ProtocolCommandSet.TINY)
        self.assertEqual(ProtocolFamily.TINY_PREFIXED.command_set, ProtocolCommandSet.TINY)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL.command_set, ProtocolCommandSet.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.LUCK_NORMAL_A4.command_set, ProtocolCommandSet.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.V5X.command_set, ProtocolCommandSet.V5X)
        self.assertEqual(ProtocolFamily.V5C.command_set, ProtocolCommandSet.V5C)
        self.assertEqual(ProtocolFamily.DCK.command_set, ProtocolCommandSet.DCK)
        self.assertEqual(ProtocolFamily.TOPRINT_HPRT_ESC.command_set, ProtocolCommandSet.TOPRINT_HPRT_ESC)
        self.assertEqual(ProtocolFamily.ELEPH_TSPL.command_set, ProtocolCommandSet.ELEPH_TSPL)
        self.assertEqual(ProtocolFamily.TOPRINT_TSPL.command_set, ProtocolCommandSet.TOPRINT_TSPL)
        self.assertEqual(ProtocolFamily.PHOMEMO_ESC.command_set, ProtocolCommandSet.PHOMEMO_ESC)

    def test_protocol_family_accepts_current_serialized_values(self) -> None:
        self.assertEqual(ProtocolFamily.from_value(None), ProtocolFamily.TINY)
        self.assertEqual(ProtocolFamily.from_value("tiny"), ProtocolFamily.TINY)
        self.assertEqual(ProtocolFamily.from_value("tiny_prefixed"), ProtocolFamily.TINY_PREFIXED)
        self.assertEqual(ProtocolFamily.from_value("luck_normal"), ProtocolFamily.LUCK_NORMAL)
        self.assertEqual(ProtocolFamily.from_value("luck_normal_a4"), ProtocolFamily.LUCK_NORMAL_A4)
        self.assertEqual(ProtocolFamily.from_value("v5x"), ProtocolFamily.V5X)
        self.assertEqual(ProtocolFamily.from_value("v5c"), ProtocolFamily.V5C)
        self.assertEqual(ProtocolFamily.from_value("dck"), ProtocolFamily.DCK)
        self.assertEqual(ProtocolFamily.from_value("toprint_hprt_esc"), ProtocolFamily.TOPRINT_HPRT_ESC)
        self.assertEqual(ProtocolFamily.from_value("eleph_tspl"), ProtocolFamily.ELEPH_TSPL)
        self.assertEqual(ProtocolFamily.from_value("toprint_tspl"), ProtocolFamily.TOPRINT_TSPL)
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
