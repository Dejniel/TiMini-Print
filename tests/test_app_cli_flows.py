from __future__ import annotations

import argparse
import asyncio
import json
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
from timiniprint.protocol import ImageEncoding, ProtocolJob
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.packet import make_packet
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


class AppCliFlowsTests(unittest.TestCase):
    def _args(self, **kwargs):
        base = dict(
            list_profiles=False,
            scan=False,
            feed=False,
            retract=False,
            serial=None,
            path=None,
            text=None,
            verbose=False,
            bluetooth=None,
            config=None,
            export_config=None,
            debug_profile=None,
            debug_dump_protocol_job=None,
            debug_image_encoding=None,
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
            pdf_page_gap=None,
            paper_mode=None,
        )
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_main_no_args_returns_2(self) -> None:
        with patch("timiniprint.app.cli.emit_startup_warnings"):
            code = cli.main([])
        self.assertEqual(code, 2)

    def test_main_dispatch_list_profiles_and_scan(self) -> None:
        args = self._args(list_profiles=True)
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.list_profiles", return_value=0) as list_profiles:
            self.assertEqual(cli.main(["--list-profiles"]), 0)
        list_profiles.assert_called_once()

        args2 = self._args(scan=True)
        with patch("timiniprint.app.cli.parse_args", return_value=args2), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ), patch("timiniprint.app.cli.scan_devices", return_value=0) as scan_devices:
            self.assertEqual(cli.main(["--scan"]), 0)
        scan_devices.assert_called_once()

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

    def test_resolve_image_encoding_override_returns_enum(self) -> None:
        self.assertIsNone(cli._resolve_image_encoding_override(self._args()))
        self.assertEqual(
            cli._resolve_image_encoding_override(self._args(debug_image_encoding="v5g_gray")),
            ImageEncoding.V5G_GRAY,
        )

    def test_print_and_motion_flows_use_connectors(self) -> None:
        args = self._args(path="x.txt", bluetooth="X6H")
        device = MagicMock()
        device.profile.use_spp = True
        device.profile.dev_dpi = 203
        device.profile_key = "x6h"
        device.address = "AA"
        device.transport_badge = "[classic]"
        device.protocol_family = "legacy"
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

    def test_export_config_writes_full_editable_profile_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/printer.json"
            args = self._args(export_config=("luck_a2", out_path))

            code = cli.export_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            exported = cli._load_config(out_path)
            self.assertEqual(exported["schema"], "timiniprint/config/v1")
            self.assertEqual(exported["profile_key"], "luck_a2")
            overrides = exported["profile_overrides"]
            self.assertEqual(overrides["default_protocol_family"], "luck_normal")
            self.assertIn("stream", overrides)
            self.assertIn("print_defaults", overrides)
            self.assertIn("energy", overrides["print_defaults"])
            self.assertIn("runtime_overrides", exported)

    def test_export_config_writes_editable_runtime_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/printer.json"
            args = self._args(export_config=("mx06", out_path))

            code = cli.export_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            exported = cli._load_config(out_path)
            runtime_overrides = exported["runtime_overrides"]
            self.assertEqual(runtime_overrides["variant"], "mx06")
            self.assertEqual(runtime_overrides["defaults_key"], "mx06")
            self.assertEqual(runtime_overrides["density"]["image"]["middle"], 180)
            self.assertTrue(runtime_overrides["capabilities"]["d2_status"])
            self.assertIsNone(exported["profile_overrides"]["print_defaults"]["density"])

    def test_config_json_path_not_found_is_reported_as_missing_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = f"{tmpdir}/missing.json"

            with self.assertRaisesRegex(RuntimeError, "Config file not found"):
                cli._device_from_config_arg(catalog, missing_path)

    def test_config_key_wins_over_same_named_local_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            current_dir = os.getcwd()
            try:
                os.chdir(tmpdir)
                cli._write_config("gt01", catalog.serialize_config(catalog.device_from_profile("x6h")))

                device = cli._device_from_config_arg(catalog, "gt01")
            finally:
                os.chdir(current_dir)

        self.assertEqual(device.profile_key, "gt01")

    def test_config_explicit_relative_path_can_load_same_named_file(self) -> None:
        catalog = PrinterCatalog.load()
        with tempfile.TemporaryDirectory() as tmpdir:
            current_dir = os.getcwd()
            try:
                os.chdir(tmpdir)
                cli._write_config("gt01", catalog.serialize_config(catalog.device_from_profile("x6h")))

                device = cli._device_from_config_arg(catalog, "./gt01")
            finally:
                os.chdir(current_dir)

        self.assertEqual(device.profile_key, "x6h")

    def test_main_rejects_export_config_combined_with_printing(self) -> None:
        args = self._args(export_config=("luck_a2", "printer.json"), path="x.txt")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-config", "luck_a2", "printer.json", "x.txt"]),
                2,
            )

    def test_main_rejects_export_config_combined_with_bluetooth(self) -> None:
        args = self._args(export_config=("luck_a2", "printer.json"), bluetooth="X6H")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-config", "luck_a2", "printer.json", "--bluetooth", "X6H"]),
                2,
            )

    def test_debug_dump_protocol_job_writes_offline_profile_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_path = f"{tmpdir}/job.json"
            args = self._args(
                debug_profile="mx06",
                debug_dump_protocol_job=dump_path,
                debug_image_encoding="v5g_gray",
                text="hello",
                darkness=5,
            )
            packet = make_packet(0xA4, b"\x35", ProtocolFamily.V5G)

            with patch(
                "timiniprint.app.cli.build_print_job",
                return_value=ProtocolJob(payload=packet),
            ) as build_job:
                code = cli.debug_dump_protocol_job(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            build_job.assert_called_once()
            self.assertEqual(build_job.call_args.kwargs["image_encoding_override"], ImageEncoding.V5G_GRAY)
            with open(dump_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["schema"], "timiniprint/debug-protocol-job/v1")
            self.assertTrue(payload["diagnostic_only"])
            self.assertEqual(payload["device"]["profile_key"], "v5g_small_203")
            self.assertEqual(payload["device"]["runtime_defaults_key"], "mx06")
            self.assertEqual(payload["device"]["protocol_family"], "v5g")
            self.assertEqual(payload["device"]["image_pipeline"]["encoding"], "v5g_dot")
            self.assertEqual(payload["settings"]["darkness"], 5)
            self.assertEqual(payload["settings"]["image_encoding_override"], "v5g_gray")
            self.assertIsNone(payload["settings"]["debug_row_markers"])
            self.assertEqual(payload["job"]["effective_image_pipeline"]["encoding"], "v5g_gray")
            self.assertEqual(payload["job"]["effective_image_pipeline"]["formats"][0], "gray4")
            self.assertIn("connect_packets", payload["transport"])
            self.assertEqual(payload["job"]["payload_bytes"], len(packet))
            self.assertEqual(payload["packets"][0]["op"], "A4")
            self.assertEqual(payload["payload_hex"], packet.hex())

    def test_main_rejects_debug_profile_without_debug_dump(self) -> None:
        args = self._args(debug_profile="gt01", text="hello")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(["--debug-profile", "gt01", "--text", "hello"]), 2)

    def test_main_rejects_debug_image_encoding_with_motion(self) -> None:
        args = self._args(feed=True, debug_image_encoding="v5g_gray")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(["--feed", "--debug-image-encoding", "v5g_gray"]), 2)

    def test_main_rejects_debug_row_markers_with_motion(self) -> None:
        args = self._args(feed=True, debug_row_markers=10)
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(cli.main(["--feed", "--debug-row-markers", "10"]), 2)

    def test_config_with_bluetooth_uses_raw_target_resolution(self) -> None:
        catalog = PrinterCatalog.load()
        config = catalog.serialize_config(catalog.device_from_profile("x6h"))
        endpoint = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = f"{tmpdir}/device.json"
            cli._write_config(config_path, config)
            args = self._args(config=config_path, bluetooth="PPA2L_3F19")

            with patch(
                "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
                AsyncMock(side_effect=[([endpoint], []), ([], [])]),
            ):
                device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "PPA2L_3F19")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")

    def test_config_without_bluetooth_uses_first_raw_target(self) -> None:
        catalog = PrinterCatalog.load()
        config = catalog.serialize_config(catalog.device_from_profile("x6h"))
        endpoint = DeviceInfo(
            name="Unknown_Printer",
            address="AA:BB:CC:DD:EE:02",
            transport=DeviceTransport.CLASSIC,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = f"{tmpdir}/device.json"
            cli._write_config(config_path, config)
            args = self._args(config=config_path)

            with patch(
                "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
                AsyncMock(side_effect=[([endpoint], []), ([], [])]),
            ):
                device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "Unknown_Printer")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:02")

    def test_config_profile_key_with_bluetooth_uses_raw_target_resolution(self) -> None:
        catalog = PrinterCatalog.load()
        endpoint = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )
        args = self._args(config="x6h", bluetooth="PPA2L_3F19")

        with patch(
            "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
            AsyncMock(side_effect=[([endpoint], []), ([], [])]),
        ):
            device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "PPA2L_3F19")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")

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
            variant="mx10",
            defaults=types.SimpleNamespace(key="mx06"),
        )
        device.detection_rule_key = "rule_mx10_v5g"
        device.profile.use_spp = False

        cli._debug_resolved_device(reporter, device, action="print")

        detail = sink.messages[-1].detail
        self.assertIn("name=MX10", detail)
        self.assertIn("profile=v5g_small_203", detail)
        self.assertIn("protocol=v5g", detail)
        self.assertIn("runtime=mx10", detail)
        self.assertIn("runtime_defaults=mx06", detail)
        self.assertIn("detection_rule=rule_mx10_v5g", detail)


if __name__ == "__main__":
    unittest.main()
