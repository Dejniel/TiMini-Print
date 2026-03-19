from __future__ import annotations

import importlib
import unittest


class EnvironmentOptionalImportsTests(unittest.TestCase):
    def test_platform_modules_import_without_optional_runtime_deps(self) -> None:
        modules = [
            "timiniprint.transport.bluetooth.adapters.macos_iobluetooth",
            "timiniprint.transport.bluetooth.adapters.windows_win32",
            "timiniprint.transport.bluetooth.adapters.windows_winrt",
        ]
        for name in modules:
            with self.subTest(module=name):
                module = importlib.import_module(name)
                self.assertIsNotNone(module)


if __name__ == "__main__":
    unittest.main()
