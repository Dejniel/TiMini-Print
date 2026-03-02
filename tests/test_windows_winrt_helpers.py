from __future__ import annotations

import builtins
import unittest
from unittest.mock import patch

from timiniprint.transport.bluetooth.adapters import windows_winrt


class WindowsWinRtHelpersTests(unittest.TestCase):
    def test_address_helpers(self) -> None:
        self.assertEqual(windows_winrt._format_bt_address(0xAABBCCDDEEFF), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(windows_winrt._parse_bt_address("AA:BB:CC:DD:EE:FF"), 0xAABBCCDDEEFF)
        self.assertIsNone(windows_winrt._parse_bt_address("bad"))
        did = "abc_AA-BB-CC-DD-EE-FF_xyz"
        self.assertEqual(windows_winrt._extract_address_from_id(did), "AA:BB:CC:DD:EE:FF")

    def test_missing_message_and_import_error(self) -> None:
        self.assertIn("winsdk", windows_winrt._winrt_missing_message())
        original_import = builtins.__import__

        def _import(name, *args, **kwargs):
            if name.startswith("winsdk"):
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_import):
            with self.assertRaisesRegex(RuntimeError, "winsdk"):
                windows_winrt._winrt_imports()


if __name__ == "__main__":
    unittest.main()
