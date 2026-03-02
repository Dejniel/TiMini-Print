from __future__ import annotations

import argparse
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from timiniprint.app import cli


class AppCliFlowsTests(unittest.TestCase):
    def _args(self, **kwargs):
        base = dict(
            list_models=False,
            scan=False,
            feed=False,
            retract=False,
            serial=None,
            path=None,
            text=None,
            verbose=False,
            bluetooth=None,
            model=None,
            force_text_mode=False,
            force_image_mode=False,
            darkness=None,
            text_font=None,
            text_columns=None,
            text_hard_wrap=False,
            trim_side_margins=True,
            trim_top_bottom_margins=True,
            pdf_pages=None,
            pdf_page_gap=None,
        )
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_main_no_args_launches_gui(self) -> None:
        with patch.object(sys, "argv", ["prog"]), patch("timiniprint.app.cli.launch_gui", return_value=0) as gui:
            code = cli.main()
        self.assertEqual(code, 0)
        self.assertEqual(gui.call_count, 1)

    def test_main_dispatch_list_models_and_scan(self) -> None:
        args = self._args(list_models=True)
        with patch.object(sys, "argv", ["prog", "--list-models"]), patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.list_models", return_value=0) as lm:
            self.assertEqual(cli.main(), 0)
        self.assertEqual(lm.call_count, 1)

        args2 = self._args(scan=True)
        with patch.object(sys, "argv", ["prog", "--scan"]), patch("timiniprint.app.cli.parse_args", return_value=args2), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.scan_devices", return_value=0) as sc:
            self.assertEqual(cli.main(), 0)
        self.assertEqual(sc.call_count, 1)

    def test_main_conflicting_args_returns_2(self) -> None:
        args = self._args(path="a.pdf", text="txt")
        with patch.object(sys, "argv", ["prog", "a.pdf", "--text", "x"]), patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(), 2)

    def test_build_print_data_text_path_and_cleanup(self) -> None:
        fake_printing = types.ModuleType("timiniprint.printing")

        class _B:
            def __init__(self, *_args, **_kwargs):
                pass

            def build_from_file(self, path: str) -> bytes:
                return ("OK:" + path.split("/")[-1]).encode("utf-8")

        class _S:
            def __init__(self, **_kwargs):
                self.blackening = 3

        fake_printing.PrintJobBuilder = _B
        fake_printing.PrintSettings = _S

        with patch.dict(sys.modules, {"timiniprint.printing": fake_printing}):
            model = MagicMock()
            data = cli.build_print_data(model, path=None, text_input="hello")
        self.assertTrue(data.startswith(b"OK:"))

    def test_print_and_motion_flows_use_backend_attempts(self) -> None:
        args = self._args(path="x.txt", bluetooth="X6H")
        resolved = MagicMock()
        resolved.model_match = MagicMock(model=MagicMock(img_mtu=180, interval_ms=1))
        resolved.paired = False
        resolved.name = "X6H"
        resolved.address = "AA"
        resolved.display_address = "AA"
        resolved.transport_label = "[classic]"
        backend = MagicMock()
        backend.connect_attempts = AsyncMock()
        backend.write = AsyncMock()
        backend.disconnect = AsyncMock()

        with patch("timiniprint.app.cli.PrinterModelRegistry.load"), patch("timiniprint.app.cli.DeviceResolver") as resolver_cls, patch(
            "timiniprint.app.cli.SppBackend", return_value=backend
        ), patch("timiniprint.app.cli.build_print_data", return_value=b"123"):
            resolver = resolver_cls.return_value
            resolver.resolve_printer_device = AsyncMock(return_value=resolved)
            resolver.build_connection_attempts.return_value = [MagicMock()]
            code = cli.print_bluetooth(args, cli._build_cli_reporter(verbose=False))
        self.assertEqual(code, 0)
        self.assertEqual(backend.connect_attempts.await_count, 1)


if __name__ == "__main__":
    unittest.main()
