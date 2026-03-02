from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, patch

from timiniprint.transport.bluetooth.adapters.linux_cmd import LinuxCommandTools


class LinuxCmdTests(unittest.TestCase):
    def test_scan_devices_parses_devices_and_paired(self) -> None:
        tools = LinuxCommandTools()

        def run_bt(args, timeout=None):
            _ = timeout
            cmd = tuple(args)
            if cmd == ("--timeout", "5", "scan", "on"):
                return ""
            if cmd == ("devices",):
                return "Device AA:BB:CC:DD:EE:01 X6H\nDevice AA:BB:CC:DD:EE:02 OTHER\n"
            if cmd == ("devices", "Paired"):
                return "Device AA:BB:CC:DD:EE:01 X6H\n"
            return ""

        with patch.object(tools, "_has_bluetoothctl", return_value=True), patch.object(
            tools, "_run_bluetoothctl", side_effect=run_bt
        ):
            devices, paired = tools.scan_devices(5.0)
        self.assertEqual(len(devices), 2)
        self.assertIn("AA:BB:CC:DD:EE:01", paired)
        self.assertTrue(devices[0].paired or devices[1].paired)

    def test_resolve_rfcomm_channels_parsing(self) -> None:
        output = """
Service Name: Serial Port
Channel: 7

Service Name: Other
Channel: 3
"""
        with patch("shutil.which", return_value="/usr/bin/sdptool"), patch(
            "subprocess.run", return_value=Mock(stdout=output)
        ):
            channels = LinuxCommandTools().resolve_rfcomm_channels("AA:BB")
        self.assertEqual(channels, [7])

        output2 = "Service Name: Other\nChannel: 2\n"
        with patch("shutil.which", return_value="/usr/bin/sdptool"), patch(
            "subprocess.run", return_value=Mock(stdout=output2)
        ):
            channels2 = LinuxCommandTools().resolve_rfcomm_channels("AA:BB")
        self.assertEqual(channels2, [2])

        with patch("shutil.which", return_value="/usr/bin/sdptool"), patch(
            "subprocess.run", side_effect=subprocess.SubprocessError("x")
        ):
            self.assertEqual(LinuxCommandTools().resolve_rfcomm_channels("AA"), [])

    def test_ensure_paired_flow(self) -> None:
        tools = LinuxCommandTools()
        with patch.object(tools, "_has_bluetoothctl", return_value=True), patch.object(
            tools, "_bluetoothctl_is_paired", side_effect=[False, True]
        ), patch.object(tools, "_bluetoothctl_pair") as pair_mock, patch.object(tools, "_bluetoothctl_trust") as trust_mock:
            tools.ensure_paired("AA")
        self.assertEqual(pair_mock.call_count, 1)
        self.assertEqual(trust_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
