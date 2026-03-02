from __future__ import annotations

import unittest
from unittest.mock import patch

from timiniprint.transport.bluetooth.adapters import windows_win32


class WindowsWin32HelpersTests(unittest.TestCase):
    def test_scan_inquiry_returns_empty_when_windll_unavailable(self) -> None:
        with patch("ctypes.WinDLL", side_effect=OSError("no dll"), create=True):
            out = windows_win32.scan_inquiry(5.0)
        self.assertEqual(out, [])

    def test_pair_device_invalid_address(self) -> None:
        self.assertFalse(windows_win32.pair_device("bad-address"))


if __name__ == "__main__":
    unittest.main()
