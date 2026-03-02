from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import build_capture_reporter
from timiniprint.app import diagnostics


class AppDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        diagnostics._WARNED = False

    def test_collect_dependency_warnings_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("""Pillow\nbleak\npyserial\ncrc8\n""", encoding="utf-8")
            with patch.object(diagnostics, "_REQUIREMENTS_PATH", req), patch.object(
                diagnostics, "_has_module", return_value=False
            ):
                warnings = diagnostics.collect_dependency_warnings()
        text = "\n".join(warnings)
        self.assertIn("Missing Pillow", text)
        self.assertIn("Missing bleak", text)
        self.assertIn("Missing pyserial", text)
        self.assertIn("Missing crc8", text)

    def test_platform_specific_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("winsdk\npyobjc-framework-IOBluetooth\n", encoding="utf-8")
            with patch.object(diagnostics, "_REQUIREMENTS_PATH", req), patch.object(
                diagnostics, "_has_module", return_value=False
            ), patch.object(diagnostics, "IS_WINDOWS", True), patch.object(diagnostics, "IS_MACOS", True):
                warnings = diagnostics.collect_dependency_warnings()
        joined = "\n".join(warnings)
        self.assertIn("winsdk", joined)
        self.assertIn("pyobjc-framework-IOBluetooth", joined)

    def test_emit_startup_warnings_only_once(self) -> None:
        reporter, sink = build_capture_reporter()
        with patch.object(diagnostics, "collect_dependency_warnings", return_value=["A", "B"]):
            diagnostics.emit_startup_warnings(reporter)
            diagnostics.emit_startup_warnings(reporter)
        self.assertEqual(len(sink.messages), 2)


if __name__ == "__main__":
    unittest.main()
