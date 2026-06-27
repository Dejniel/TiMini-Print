from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import tempfile
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import build_capture_reporter, install_crc8_stub

install_crc8_stub()

from timiniprint.app import cli
from timiniprint.devices import BluetoothTarget, PrinterCatalog
from timiniprint.transport.bluetooth import BluetoothScanResult
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
from timiniprint.update_check import UpdateCheckResult


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
            printer_config=None,
            printer_model=None,
            export_printer_config=None,
            debug_row_markers=None,
            force_text_mode=False,
            force_image_mode=False,
            darkness=None,
            text_font=None,
            text_columns=None,
            text_hard_wrap=False,
            trim_side_margins=True,
            trim_top_bottom_margins=True,
            pdf_pages=None,
            page_gap=None,
            paper_mode=None,
        )
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_main_no_args_returns_2(self) -> None:
        with patch("timiniprint.app.cli.emit_startup_warnings"):
            code = cli.main([])
        self.assertEqual(code, 2)

    def test_main_dispatch_list_models_and_scan(self) -> None:
        args_models = self._args(list_models=True)
        with patch("timiniprint.app.cli.parse_args", return_value=args_models), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.emit_update_warning"
        ), patch("timiniprint.app.cli.list_models", return_value=0) as list_models:
            self.assertEqual(cli.main(["--list-models"]), 0)
        list_models.assert_called_once()

        args2 = self._args(scan=True)
        with patch("timiniprint.app.cli.parse_args", return_value=args2), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.emit_update_warning"
        ), patch("timiniprint.app.cli.scan_devices", return_value=0) as scan_devices:
            self.assertEqual(cli.main(["--scan"]), 0)
        scan_devices.assert_called_once()

    def test_list_models_includes_origin_app_names(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            code = cli.list_models()

        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("pocket_printer: ", text)
        self.assertIn("[app: Tiny Print]", text)
        self.assertIn("toprint_tspl_p1: P1 [app: ToPrint]", text)

    def test_scan_lists_ambiguous_supported_source_variants(self) -> None:
        reporter, _sink = build_capture_reporter()
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[
                DeviceInfo(
                    name="P1",
                    address="AA:BB:CC:DD:EE:01",
                    transport=DeviceTransport.CLASSIC,
                )
            ],
        )
        output = io.StringIO()

        with patch(
            "timiniprint.transport.bluetooth.discovery.BluetoothDiscovery.scan_report",
            new=AsyncMock(return_value=result),
        ), contextlib.redirect_stdout(output):
            code = cli.scan_devices(reporter)

        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("model: pocket_printer; app: Tiny Print", text)
        self.assertIn("model: toprint_tspl_p1; app: ToPrint", text)

    def test_emit_update_warning_reports_available_release(self) -> None:
        reporter, sink = build_capture_reporter()

        with patch("timiniprint.app.cli.should_check_for_updates", return_value=True), patch(
            "timiniprint.app.cli.check_for_updates",
            return_value=UpdateCheckResult(
                current_version="0.5",
                latest_version="v0.6",
                release_url="https://example.test/releases/v0.6",
            ),
        ):
            cli.emit_update_warning(reporter)

        self.assertEqual(len(sink.messages), 1)
        self.assertEqual(sink.messages[0].short, "Update available: v0.6")
        self.assertIn("https://example.test/releases/v0.6", sink.messages[0].detail)

    def test_main_conflicting_args_returns_2(self) -> None:
        args = self._args(path="a.pdf", text="txt")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(["a.pdf", "--text", "x"]), 2)

    def test_main_accepts_debug_row_markers_with_print_input(self) -> None:
        args = self._args(text="hello", debug_row_markers=10)
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.print_bluetooth", return_value=0) as print_bluetooth:
            self.assertEqual(cli.main(["--debug-row-markers", "10", "--text", "hello"]), 0)
        print_bluetooth.assert_called_once()

    def test_build_print_job_text_path_and_cleanup(self) -> None:
        device = MagicMock()

        class _B:
            def __init__(self, *_args, **_kwargs):
                pass

            def build_from_file(self, path: str):
                from timiniprint.protocol import ProtocolJob

                return ProtocolJob(payload=("OK:" + path.split("/")[-1]).encode("utf-8"))

        with patch.object(cli, "PrintJobBuilder", _B), patch.object(
            cli,
            "PrintSettings",
            lambda **_kwargs: types.SimpleNamespace(blackening=3),
        ):
            job = cli.build_print_job(device, path=None, text_input="hello")
        self.assertTrue(job.payload.startswith(b"OK:"))

    def test_create_print_job_builder_passes_debug_row_markers_to_settings(self) -> None:
        device = MagicMock()

        with patch.object(cli, "PrintJobBuilder") as builder_cls, patch.object(
            cli,
            "PrintSettings",
        ) as settings_cls:
            settings = types.SimpleNamespace(blackening=3)
            settings_cls.return_value = settings
            cli.create_print_job_builder(
                device,
                debug_row_markers_interval=10,
            )

        settings_cls.assert_called_once()
        self.assertEqual(settings_cls.call_args.kwargs["debug_row_markers_interval"], 10)
        builder_cls.assert_called_once()

    def test_resolve_paper_mode_returns_enum(self) -> None:
        self.assertIsNone(cli._resolve_paper_mode(self._args()))
        self.assertEqual(cli._resolve_paper_mode(self._args(paper_mode="tag")).value, "tag")

    def test_print_and_motion_flows_use_connectors(self) -> None:
        args = self._args(path="x.txt", bluetooth="X6H")
        device = MagicMock()
        device.profile.use_spp = True
        device.profile.dev_dpi = 203
        device.profile_key = "x6h"
        device.address = "AA"
        device.transport_badge = "[classic]"
        device.protocol_family = "tiny"
        connection = MagicMock()
        connection.send = AsyncMock()
        connection.disconnect = AsyncMock()
        builder = MagicMock()
        job = types.SimpleNamespace(payload=b"123", runtime_controller=object())
        builder.build_from_file.return_value = job

        with patch("timiniprint.app.cli.PrinterCatalog.load"), patch(
            "timiniprint.app.cli._resolve_bluetooth_device",
            new=AsyncMock(return_value=device),
        ), patch(
            "timiniprint.app.cli.BleakBluetoothConnector"
        ) as connector_cls, patch(
            "timiniprint.app.cli.create_print_job_builder", return_value=builder
        ), patch("timiniprint.app.cli.send_prepared_job", new=AsyncMock()) as send_job:
            connector_cls.return_value.connect = AsyncMock(return_value=connection)

            code = cli.print_bluetooth(args, cli._build_cli_reporter(verbose=False))
            self.assertEqual(code, 0)
            connector_cls.return_value.connect.assert_awaited_once_with(device)
            send_job.assert_awaited_once()
            self.assertIs(send_job.await_args.args[0], device)
            self.assertIs(send_job.await_args.args[1], connection)
            self.assertIs(send_job.await_args.args[2], job)
            connection.disconnect.assert_awaited_once()

            motion = self._args(feed=True, bluetooth="X6H")
            code = cli.paper_motion_bluetooth(motion, "feed", cli._build_cli_reporter(verbose=False))
            self.assertEqual(code, 0)
            connection.send.assert_awaited_once()

    def test_export_printer_config_writes_full_editable_profile_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/printer.json"
            args = self._args(export_printer_config=("luck_a2", out_path))

            code = cli.export_printer_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            exported = cli._load_printer_config(out_path)
            self.assertEqual(exported["schema"], "timiniprint/printer-config/v1")
            self.assertEqual(exported["model_key"], "luck_a2")
            self.assertEqual(exported["profile_key"], "luck_a2")
            overrides = exported["profile_overrides"]
            self.assertEqual(overrides["protocol_default"]["type"], "luck_normal")
            self.assertIn("stream", overrides)
            self.assertIn("print_defaults", overrides)
            self.assertIn("energy", overrides["print_defaults"])
            self.assertIn("runtime_overrides", exported)

    def test_export_printer_config_writes_editable_runtime_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/printer.json"
            args = self._args(export_printer_config=("mx10", out_path))

            code = cli.export_printer_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            exported = cli._load_printer_config(out_path)
            self.assertEqual(exported["model_key"], "mx10")
            runtime_overrides = exported["runtime_overrides"]
            self.assertEqual(runtime_overrides["control_algorithm"], "mx10")
            self.assertEqual(runtime_overrides["preset_key"], "mx10_mx06")
            self.assertEqual(runtime_overrides["density"]["image"]["middle"], 180)
            self.assertTrue(runtime_overrides["capabilities"]["d2_status"])
            self.assertNotIn("runtime_presets", exported["profile_overrides"])
            self.assertIsNone(exported["profile_overrides"]["print_defaults"]["density"])

    def test_printer_config_json_path_not_found_is_reported_as_missing_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = f"{tmpdir}/missing.json"

            with self.assertRaisesRegex(RuntimeError, "Printer config file not found"):
                cli._device_from_printer_config_arg(catalog, missing_path)

    def test_printer_model_key_wins_over_same_named_local_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            current_dir = os.getcwd()
            try:
                os.chdir(tmpdir)
                cli._write_printer_config("gt01", catalog.serialize_printer_config(catalog.device_from_profile("x6h")))

                device = cli._device_from_printer_model_arg(catalog, "gt01")
            finally:
                os.chdir(current_dir)

        self.assertEqual(device.profile_key, "gt01")

    def test_printer_config_explicit_relative_path_can_load_same_named_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            current_dir = os.getcwd()
            try:
                os.chdir(tmpdir)
                cli._write_printer_config("gt01", catalog.serialize_printer_config(catalog.device_from_profile("x6h")))

                device = cli._device_from_printer_config_arg(catalog, "./gt01")
            finally:
                os.chdir(current_dir)

        self.assertEqual(device.profile_key, "x6h")

    def test_main_rejects_export_printer_config_combined_with_printing(self) -> None:
        args = self._args(export_printer_config=("luck_a2", "printer.json"), path="x.txt")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-printer-config", "luck_a2", "printer.json", "x.txt"]),
                2,
            )

    def test_main_rejects_export_printer_config_combined_with_bluetooth(self) -> None:
        args = self._args(export_printer_config=("luck_a2", "printer.json"), bluetooth="X6H")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-printer-config", "luck_a2", "printer.json", "--bluetooth", "X6H"]),
                2,
            )

    def test_main_rejects_debug_row_markers_with_motion(self) -> None:
        args = self._args(feed=True, debug_row_markers=10)
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(["--feed", "--debug-row-markers", "10"]), 2)

    def test_printer_config_with_bluetooth_uses_raw_target_resolution(self) -> None:
        catalog = PrinterCatalog.load()
        printer_config = catalog.serialize_printer_config(catalog.device_from_profile("x6h"))
        endpoint = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            printer_config_path = f"{tmpdir}/device.json"
            cli._write_printer_config(printer_config_path, printer_config)
            args = self._args(printer_config=printer_config_path, bluetooth="PPA2L_3F19")

            with patch(
                "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
                AsyncMock(side_effect=[([endpoint], []), ([], [])]),
            ):
                device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "PPA2L_3F19")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")

    def test_printer_config_without_bluetooth_uses_first_raw_target(self) -> None:
        catalog = PrinterCatalog.load()
        printer_config = catalog.serialize_printer_config(catalog.device_from_profile("x6h"))
        endpoint = DeviceInfo(
            name="Unknown_Printer",
            address="AA:BB:CC:DD:EE:02",
            transport=DeviceTransport.CLASSIC,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            printer_config_path = f"{tmpdir}/device.json"
            cli._write_printer_config(printer_config_path, printer_config)
            args = self._args(printer_config=printer_config_path)

            with patch(
                "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
                AsyncMock(side_effect=[([endpoint], []), ([], [])]),
            ):
                device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "Unknown_Printer")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:02")

    def test_printer_model_with_bluetooth_uses_raw_target_resolution(self) -> None:
        catalog = PrinterCatalog.load()
        endpoint = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )
        args = self._args(printer_model="luck_ppa2l", bluetooth="PPA2L_3F19")

        with patch(
            "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([endpoint], []), ([], [])]),
        ):
            device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "luck_ppa2l")
        self.assertEqual(device.display_name, "PPA2L_3F19")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")

    def test_main_rejects_printer_model_with_printer_config(self) -> None:
        args = self._args(path="x.txt", printer_model="gt01", printer_config="printer.json")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--printer-model", "gt01", "--printer-config", "printer.json", "x.txt"]),
                2,
            )

    def test_debug_resolved_device_includes_runtime_details(self) -> None:
        reporter, sink = build_capture_reporter()
        device = MagicMock()
        device.display_name = "MX10"
        device.address = "AA:BB:CC:DD:EE:59"
        device.transport_badge = "[ble]"
        device.profile_key = "v5g_small_203"
        device.protocol_family.value = "v5g"
        device.protocol_variant = None
        device.runtime_settings = types.SimpleNamespace(
            control_algorithm="mx10",
            preset=types.SimpleNamespace(key="mx10_mx06"),
        )
        device.model_key = "mx10"
        device.origin_app_packages = ("com.fun.mxw",)
        device.profile.use_spp = False

        cli._debug_resolved_device(reporter, device, action="print")

        detail = sink.messages[-1].detail
        self.assertIn("name=MX10", detail)
        self.assertIn("profile=v5g_small_203", detail)
        self.assertIn("protocol=v5g", detail)
        self.assertIn("runtime=mx10", detail)
        self.assertIn("runtime_preset=mx10_mx06", detail)
        self.assertIn("model=mx10", detail)
        self.assertIn("origin_app_packages=com.fun.mxw", detail)


if __name__ == "__main__":
    unittest.main()
