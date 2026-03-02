from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub


class ProtocolCommandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.commands = importlib.import_module("timiniprint.protocol.commands")

    def test_make_packet_header_and_new_format_prefix(self) -> None:
        payload = b"\x01\x02\x03"
        packet_old = self.commands.make_packet(0xA2, payload, new_format=False)
        packet_new = self.commands.make_packet(0xA2, payload, new_format=True)

        self.assertTrue(packet_old.startswith(bytes([0x51, 0x78, 0xA2, 0x00, 0x03, 0x00])))
        self.assertEqual(packet_old[-1], 0xFF)
        self.assertEqual(packet_new[0], 0x12)
        self.assertEqual(packet_new[1:], packet_old)

    def test_blackening_cmd_clamps_range(self) -> None:
        low = self.commands.blackening_cmd(0, False)
        high = self.commands.blackening_cmd(99, False)
        self.assertIn(bytes([0x31]), low)
        self.assertIn(bytes([0x35]), high)

    def test_energy_cmd_empty_for_non_positive(self) -> None:
        self.assertEqual(self.commands.energy_cmd(0, False), b"")
        self.assertEqual(self.commands.energy_cmd(-1, True), b"")

    def test_paper_payload_for_dpi_300_and_default(self) -> None:
        cmd_300 = self.commands.paper_cmd(300, False)
        cmd_203 = self.commands.paper_cmd(203, False)
        self.assertIn(bytes([0x48, 0x00]), cmd_300)
        self.assertIn(bytes([0x30, 0x00]), cmd_203)

    def test_basic_command_ids(self) -> None:
        self.assertEqual(self.commands.print_mode_cmd(True, False)[2], 0xBE)
        self.assertEqual(self.commands.feed_paper_cmd(7, False)[2], 0xBD)
        self.assertEqual(self.commands.dev_state_cmd(False)[2], 0xA3)
        self.assertEqual(self.commands.advance_paper_cmd(203, False)[2], 0xA1)
        self.assertEqual(self.commands.retract_paper_cmd(203, False)[2], 0xA0)


if __name__ == "__main__":
    unittest.main()
