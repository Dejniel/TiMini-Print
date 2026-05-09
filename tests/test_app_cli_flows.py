from __future__ import annotations

import argparse
import asyncio
import tempfile
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import install_crc8_stub

install_crc8_stub()

from timiniprint.app import cli
from timiniprint.devices import BluetoothTarget, PrinterCatalog
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
            device_config=None,
            export_device_config=None,
            export_profile_config=None,
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

    def test_export_device_config_uses_resolved_device(self) -> None:
        device = MagicMock()
        device.profile_key = "x6h"
        device.protocol_family.value = "legacy"

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/device.json"
            args = self._args(export_device_config=out_path, bluetooth="X6H")
            with patch("timiniprint.app.cli.PrinterCatalog.load") as load_catalog, patch(
                "timiniprint.app.cli._resolve_bluetooth_device",
                new=AsyncMock(return_value=device),
            ):
                catalog = load_catalog.return_value
                catalog.serialize_device_config.return_value = {"schema": "demo"}

                code = cli.export_device_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            catalog.serialize_device_config.assert_called_once_with(device)

    def test_export_profile_config_writes_requested_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = f"{tmpdir}/device.json"
            args = self._args(export_profile_config=("luck_a2", out_path))

            code = cli.export_profile_config(args, cli._build_cli_reporter(verbose=False))

            self.assertEqual(code, 0)
            exported = cli._load_device_config(out_path)
            self.assertEqual(exported["profile_key"], "luck_a2")
            self.assertEqual(exported["protocol_family"], "luck_normal")

    def test_main_rejects_export_profile_config_combined_with_printing(self) -> None:
        args = self._args(export_profile_config=("luck_a2", "printer.json"), path="x.txt")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-profile-config", "luck_a2", "printer.json", "x.txt"]),
                2,
            )

    def test_main_rejects_export_profile_config_combined_with_bluetooth(self) -> None:
        args = self._args(export_profile_config=("luck_a2", "printer.json"), bluetooth="X6H")
        with patch("timiniprint.app.cli.parse_args", return_value=args), patch(
            "timiniprint.app.cli.emit_startup_warnings"
        ):
            self.assertEqual(
                cli.main(["--export-profile-config", "luck_a2", "printer.json", "--bluetooth", "X6H"]),
                2,
            )

    def test_device_config_with_bluetooth_uses_raw_target_resolution(self) -> None:
        catalog = PrinterCatalog.load()
        config = catalog.serialize_device_config(catalog.device_from_profile("x6h"))
        endpoint = DeviceInfo(
            name="PPA2L_3F19",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = f"{tmpdir}/device.json"
            cli._write_device_config(config_path, config)
            args = self._args(device_config=config_path, bluetooth="PPA2L_3F19")

            with patch(
                "timiniprint.transport.bluetooth.discovery.SppBackend.scan_with_failures",
                AsyncMock(side_effect=[([endpoint], []), ([], [])]),
            ):
                device = asyncio.run(cli._resolve_bluetooth_device(args, catalog))

        self.assertEqual(device.profile_key, "x6h")
        self.assertEqual(device.display_name, "PPA2L_3F19")
        self.assertIsInstance(device.transport_target, BluetoothTarget)
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:01")


if __name__ == "__main__":
    unittest.main()
