from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping
from typing import Optional, Sequence

from .. import reporting
from ..devices import BluetoothTarget, PrinterCatalog, PrinterDevice, SerialTarget
from ..printing.builder import PrintJobBuilder
from ..printing.debug_dump import build_protocol_job_debug_dump
from ..printing.runtime.base import PreparedRuntimeContext
from ..printing.runtime.prepare import prepare_connection_runtime
from ..printing.send import send_prepared_job
from ..printing.settings import PrintSettings
from ..protocol import ImageEncoding, PaperMode, PrinterProtocol, ProtocolJob
from ..transport.bluetooth import BluetoothDiscovery, BleakBluetoothConnector
from ..transport.bluetooth.types import DeviceTransport
from ..transport.serial import SerialConnector
from .diagnostics import emit_startup_warnings


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TiMini Print: Bluetooth printing for TiMini-compatible thermal printers."
    )
    parser.add_argument("path", nargs="?", help="File to print (.png/.jpg/.pdf/.txt)")
    parser.add_argument("--bluetooth", help="Bluetooth name or address (default: first supported printer)")
    parser.add_argument("--serial", metavar="PATH", help="Serial port path to bypass Bluetooth (e.g. /dev/rfcomm0)")
    parser.add_argument(
        "--device-config",
        metavar="PATH",
        help="Path to a JSON printer device config used for serial printing or manual runtime overrides",
    )
    parser.add_argument(
        "--export-device-config",
        metavar="PATH",
        help="Resolve the current printer device config, write it as JSON, and exit",
    )
    parser.add_argument(
        "--export-profile-config",
        nargs=2,
        metavar=("KEY", "PATH"),
        help="Write a fresh device config JSON for a known printer profile key and exit",
    )
    parser.add_argument(
        "--debug-profile",
        metavar="KEY",
        help="Use a known printer profile for offline debug actions; never connects to a printer",
    )
    parser.add_argument(
        "--debug-dump-protocol-job",
        metavar="PATH",
        help="Write an offline protocol-job debug dump as JSON and exit; this is not a print/replay format",
    )
    parser.add_argument(
        "--debug-image-encoding",
        choices=[encoding.value for encoding in ImageEncoding],
        help="Debug only: override the protocol image encoding when building a print job",
    )
    parser.add_argument("--scan", action="store_true", help="List nearby supported printers and exit")
    parser.add_argument("--list-profiles", action="store_true", help="List known printer profiles and exit")
    parser.add_argument("--text", metavar="TEXT", help="Print raw text instead of a file path")
    parser.add_argument("--text-font", metavar="PATH", help="Path to a .ttf/.otf font used for text rendering (default: monospace bold)")
    parser.add_argument("--text-columns", type=int, metavar="N", help="Target number of characters per line for text rendering")
    parser.add_argument("--text-hard-wrap", action="store_true", help="Disable whitespace word wrapping (enable hard-wrap by width) for text rendering (.txt or --text)")
    parser.add_argument("--pdf-pages", metavar="PAGES", help="PDF pages to print (e.g. 1,3-5). Default: all pages")
    parser.add_argument("--pdf-page-gap", type=int, metavar="MM", help="Extra vertical gap between PDF pages in millimeters (default: 5)")
    parser.add_argument("--no-trim-side-margins", action="store_false", dest="trim_side_margins", help="Disable auto-trimming white side margins for images and PDFs")
    parser.add_argument("--no-trim-top-bottom-margins", action="store_false", dest="trim_top_bottom_margins", help="Disable auto-trimming white top/bottom margins for images and PDFs")
    parser.add_argument("--darkness", type=int, choices=range(1, 6), help="Print darkness (1-5)")
    parser.add_argument(
        "--paper-mode",
        choices=[mode.value for mode in PaperMode],
        help="Override media mode for protocol families that support it",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logs (CLI only)")
    parser.set_defaults(trim_side_margins=True)
    parser.set_defaults(trim_top_bottom_margins=True)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--force-text-mode", action="store_true", help="Force printer protocol text mode")
    mode_group.add_argument("--force-image-mode", action="store_true", help="Force printer protocol image mode")
    motion_group = parser.add_mutually_exclusive_group()
    motion_group.add_argument("--feed", action="store_true", help="Advance paper")
    motion_group.add_argument("--retract", action="store_true", help="Retract paper")
    parser.epilog = "If any CLI options/arguments are provided, the GUI will not be launched."
    return parser.parse_args(argv)


def list_profiles() -> int:
    catalog = PrinterCatalog.load()
    for profile in catalog.profiles:
        print(profile.profile_key)
    return 0


def _load_device_config(path: str) -> Mapping[str, object]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Device config JSON must contain an object at the top level")
    return raw


def _write_device_config(path: str, config: Mapping[str, object]) -> None:
    Path(path).write_text(
        json.dumps(dict(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def scan_devices(reporter: reporting.Reporter) -> int:
    async def run() -> None:
        catalog = PrinterCatalog.load()
        discovery = BluetoothDiscovery(catalog)
        result = await discovery.scan_report(
            include_classic=True,
            include_ble=True,
        )
        for failure in result.failures:
            if failure.transport == DeviceTransport.BLE:
                reporter.warning(reporting.WARNING_SCAN_BLE_FAILED, detail=str(failure.error))
            else:
                reporter.warning(reporting.WARNING_SCAN_CLASSIC_FAILED, detail=str(failure.error))
        for device in result.devices:
            name = device.display_name or ""
            transport_badge = f" {device.transport_badge}" if device.transport_badge else ""
            status = " [unpaired]" if device.paired is False else ""
            profile = f" [profile: {device.profile_key}]"
            if name:
                print(f"{name}{profile} ({device.address}){transport_badge}{status}")
            else:
                print(f"{device.address}{profile}{transport_badge}{status}")

    try:
        asyncio.run(run())
    except Exception as exc:
        reporter.error(reporting.ERROR_SCAN_FAILED, detail=str(exc), exc=exc)
        return 2
    return 0


def create_print_job_builder(
    device: PrinterDevice,
    text_mode: Optional[bool] = None,
    blackening: Optional[int] = None,
    text_font: Optional[str] = None,
    text_columns: Optional[int] = None,
    text_wrap: bool = True,
    trim_side_margins: bool = True,
    trim_top_bottom_margins: bool = True,
    pdf_pages: Optional[str] = None,
    pdf_page_gap_mm: int = 5,
    paper_mode: Optional[PaperMode] = None,
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
    image_encoding_override: Optional[ImageEncoding] = None,
) -> PrintJobBuilder:
    settings = PrintSettings(
        text_mode=text_mode,
        text_font=text_font,
        text_columns=text_columns,
        text_wrap=text_wrap,
        trim_side_margins=trim_side_margins,
        trim_top_bottom_margins=trim_top_bottom_margins,
        pdf_pages=pdf_pages,
        pdf_page_gap_mm=pdf_page_gap_mm,
        paper_mode=paper_mode,
        image_encoding_override=image_encoding_override,
    )
    if blackening is not None:
        settings.blackening = blackening
    return PrintJobBuilder(device, settings=settings, runtime_context=runtime_context)


def build_print_job(
    device: PrinterDevice,
    path: Optional[str],
    text_mode: Optional[bool] = None,
    blackening: Optional[int] = None,
    text_input: Optional[str] = None,
    text_font: Optional[str] = None,
    text_columns: Optional[int] = None,
    text_wrap: bool = True,
    trim_side_margins: bool = True,
    trim_top_bottom_margins: bool = True,
    pdf_pages: Optional[str] = None,
    pdf_page_gap_mm: int = 5,
    paper_mode: Optional[PaperMode] = None,
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
    image_encoding_override: Optional[ImageEncoding] = None,
) -> ProtocolJob:
    builder = create_print_job_builder(
        device=device,
        text_mode=text_mode,
        blackening=blackening,
        text_font=text_font,
        text_columns=text_columns,
        text_wrap=text_wrap,
        trim_side_margins=trim_side_margins,
        trim_top_bottom_margins=trim_top_bottom_margins,
        pdf_pages=pdf_pages,
        pdf_page_gap_mm=pdf_page_gap_mm,
        paper_mode=paper_mode,
        runtime_context=runtime_context,
        image_encoding_override=image_encoding_override,
    )
    if text_input is None:
        if not path:
            raise RuntimeError("Missing file path")
        return builder.build_from_file(path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write(text_input)
            temp_path = handle.name
        return builder.build_from_file(temp_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def build_paper_motion_job(device: PrinterDevice, action: str) -> ProtocolJob:
    return PrinterProtocol(device).build_paper_motion(action)


async def _resolve_bluetooth_device(
    args: argparse.Namespace,
    catalog: PrinterCatalog,
) -> PrinterDevice:
    discovery = BluetoothDiscovery(catalog)
    config = _load_device_config(args.device_config) if args.device_config else None
    if config is None:
        return await discovery.resolve_device(args.bluetooth)
    if args.bluetooth:
        detected = await discovery.resolve_transport_target(args.bluetooth)
        return catalog.device_from_config(
            config,
            transport_target=detected.transport_target,
            display_name=detected.display_name,
        )
    device = catalog.device_from_config(config)
    if not isinstance(device.transport_target, BluetoothTarget):
        raise RuntimeError(
            "Bluetooth printing with --device-config requires either --bluetooth "
            "or a saved Bluetooth transport target in the config"
        )
    return device


def _resolve_serial_device(
    args: argparse.Namespace,
    catalog: PrinterCatalog,
) -> PrinterDevice:
    if not args.device_config:
        raise RuntimeError(
            "Serial printing requires --device-config "
            "(export one first with --export-device-config or --export-profile-config)"
        )
    return catalog.device_from_config(
        _load_device_config(args.device_config),
        transport_target=SerialTarget(args.serial),
    )


def _resolve_debug_dump_device(
    args: argparse.Namespace,
    catalog: PrinterCatalog,
) -> PrinterDevice:
    if args.debug_profile and args.device_config:
        raise RuntimeError("Use either --debug-profile or --device-config for debug dumps, not both")
    if args.debug_profile:
        return catalog.device_from_profile(args.debug_profile)
    if args.device_config:
        return catalog.device_from_config(_load_device_config(args.device_config), transport_target=None)
    if args.bluetooth:
        device = catalog.detect_device(args.bluetooth)
        if device is None:
            raise RuntimeError(f"Could not detect printer model from name '{args.bluetooth}'")
        return device
    raise RuntimeError(
        "--debug-dump-protocol-job requires --debug-profile KEY, --device-config PATH, "
        "or --bluetooth NAME"
    )


def _field_value(value) -> object:
    return value.value if hasattr(value, "value") else value


def _debug_resolved_device(
    reporter: reporting.Reporter,
    device: PrinterDevice,
    *,
    action: str,
) -> None:
    runtime_density_profile = device.runtime_density_profile
    reporter.debug(
        short="Device",
        detail=(
            f"Resolved device for {action}: "
            f"name={device.display_name or '<unknown>'} "
            f"address={device.address or '<unknown>'} "
            f"transport={device.transport_badge or '<unknown>'} "
            f"profile={device.profile_key} "
            f"protocol={_field_value(device.protocol_family)} "
            f"variant={device.protocol_variant or '<none>'} "
            f"runtime={device.runtime_variant or '<none>'} "
            f"runtime_density_profile={getattr(runtime_density_profile, 'profile_key', None) or '<none>'} "
            f"detection_rule={device.detection_rule_key or '<none>'} "
            f"use_spp={device.profile.use_spp}"
        ),
    )


def export_device_config(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()

    async def run() -> None:
        if args.serial:
            device = _resolve_serial_device(args, catalog)
        else:
            device = await _resolve_bluetooth_device(args, catalog)
        _write_device_config(
            args.export_device_config,
            catalog.serialize_device_config(device),
        )
        reporter.debug(
            short="Config",
            detail=(
                "Exported device config: "
                f"path={args.export_device_config} "
                f"profile={device.profile_key} "
                f"family={device.protocol_family.value}"
            ),
        )

    asyncio.run(run())
    return 0


def export_profile_config(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()
    profile_key, out_path = args.export_profile_config
    device = catalog.device_from_profile(profile_key)
    _write_device_config(
        out_path,
        catalog.serialize_device_config(device),
    )
    reporter.debug(
        short="Config",
        detail=(
            "Exported profile config: "
            f"path={out_path} "
            f"profile={device.profile_key} "
            f"family={device.protocol_family.value}"
        ),
    )
    return 0


def debug_dump_protocol_job(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()
    device = _resolve_debug_dump_device(args, catalog)
    _debug_resolved_device(reporter, device, action="debug-dump")
    job = build_print_job(
        device,
        args.path,
        text_mode=_resolve_text_mode(args),
        blackening=_resolve_blackening(args),
        text_input=_resolve_text_input(args),
        text_font=_resolve_text_font(args),
        text_columns=_resolve_text_columns(args),
        text_wrap=_resolve_text_wrap(args),
        trim_side_margins=_resolve_trim_side_margins(args),
        trim_top_bottom_margins=_resolve_trim_top_bottom_margins(args),
        pdf_pages=_resolve_pdf_pages(args),
        pdf_page_gap_mm=_resolve_pdf_page_gap(args),
        paper_mode=_resolve_paper_mode(args),
        image_encoding_override=_resolve_image_encoding_override(args),
    )
    paper_mode = _resolve_paper_mode(args)
    dump = build_protocol_job_debug_dump(
        device,
        job,
        settings={
            "text_mode": _resolve_text_mode(args),
            "darkness": _resolve_blackening(args),
            "paper_mode": None if paper_mode is None else paper_mode.value,
            "image_encoding_override": args.debug_image_encoding,
            "text_input": args.text is not None,
            "path": args.path,
        },
    )
    Path(args.debug_dump_protocol_job).write_text(
        json.dumps(dump, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reporter.debug(
        short="Debug",
        detail=(
            "Wrote protocol job debug dump: "
            f"path={args.debug_dump_protocol_job} "
            f"profile={device.profile_key} "
            f"family={device.protocol_family.value} "
            f"bytes={len(job.payload)}"
        ),
    )
    return 0


def _resolve_text_mode(args: argparse.Namespace) -> Optional[bool]:
    if args.force_text_mode:
        return True
    if args.force_image_mode:
        return False
    return None


def _resolve_blackening(args: argparse.Namespace) -> Optional[int]:
    return args.darkness


def _resolve_text_input(args: argparse.Namespace) -> Optional[str]:
    if args.text is None:
        return None
    return args.text


def _resolve_text_font(args: argparse.Namespace) -> Optional[str]:
    if args.text_font:
        return args.text_font
    return None


def _resolve_text_columns(args: argparse.Namespace) -> Optional[int]:
    if args.text_columns is None:
        return None
    if args.text_columns < 1:
        raise ValueError("Text columns must be at least 1")
    return args.text_columns


def _resolve_text_wrap(args: argparse.Namespace) -> bool:
    return not args.text_hard_wrap


def _resolve_pdf_pages(args: argparse.Namespace) -> Optional[str]:
    if not args.pdf_pages:
        return None
    return args.pdf_pages


def _resolve_pdf_page_gap(args: argparse.Namespace) -> int:
    if args.pdf_page_gap is None:
        return 5
    if args.pdf_page_gap < 0:
        raise ValueError("PDF page gap must be >= 0 mm")
    return args.pdf_page_gap


def _resolve_paper_mode(args: argparse.Namespace) -> PaperMode | None:
    if not args.paper_mode:
        return None
    return PaperMode(args.paper_mode)


def _resolve_image_encoding_override(args: argparse.Namespace) -> ImageEncoding | None:
    if not args.debug_image_encoding:
        return None
    return ImageEncoding(args.debug_image_encoding)


def _resolve_trim_side_margins(args: argparse.Namespace) -> bool:
    return bool(args.trim_side_margins)


def _resolve_trim_top_bottom_margins(args: argparse.Namespace) -> bool:
    return bool(args.trim_top_bottom_margins)


def _resolve_paper_motion_action(args: argparse.Namespace) -> Optional[str]:
    if args.feed:
        return "feed"
    if args.retract:
        return "retract"
    return None


def print_bluetooth(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()

    async def run() -> None:
        device = await _resolve_bluetooth_device(args, catalog)
        _debug_resolved_device(reporter, device, action="print")
        connection = await BleakBluetoothConnector(reporter=reporter).connect(device)
        try:
            runtime_context = await prepare_connection_runtime(device, connection, reporter=reporter)
            job = build_print_job(
                device,
                args.path,
                text_mode=_resolve_text_mode(args),
                blackening=_resolve_blackening(args),
                text_input=_resolve_text_input(args),
                text_font=_resolve_text_font(args),
                text_columns=_resolve_text_columns(args),
                text_wrap=_resolve_text_wrap(args),
                trim_side_margins=_resolve_trim_side_margins(args),
                trim_top_bottom_margins=_resolve_trim_top_bottom_margins(args),
                pdf_pages=_resolve_pdf_pages(args),
                pdf_page_gap_mm=_resolve_pdf_page_gap(args),
                paper_mode=_resolve_paper_mode(args),
                image_encoding_override=_resolve_image_encoding_override(args),
                runtime_context=runtime_context,
            )
            await send_prepared_job(device, connection, job, reporter=reporter)
        finally:
            try:
                await connection.disconnect()
            except Exception as exc:
                reporter.debug(short="Bluetooth", detail=f"Disconnect cleanup failed: {exc}")

    asyncio.run(run())
    return 0


def print_serial(args: argparse.Namespace, reporter: reporting.Reporter) -> int:
    catalog = PrinterCatalog.load()
    device = _resolve_serial_device(args, catalog)

    async def run() -> None:
        connection = await SerialConnector(reporter=reporter).connect(device)
        try:
            runtime_context = await prepare_connection_runtime(device, connection, reporter=reporter)
            job = build_print_job(
                device,
                args.path,
                text_mode=_resolve_text_mode(args),
                blackening=_resolve_blackening(args),
                text_input=_resolve_text_input(args),
                text_font=_resolve_text_font(args),
                text_columns=_resolve_text_columns(args),
                text_wrap=_resolve_text_wrap(args),
                trim_side_margins=_resolve_trim_side_margins(args),
                trim_top_bottom_margins=_resolve_trim_top_bottom_margins(args),
                pdf_pages=_resolve_pdf_pages(args),
                pdf_page_gap_mm=_resolve_pdf_page_gap(args),
                paper_mode=_resolve_paper_mode(args),
                image_encoding_override=_resolve_image_encoding_override(args),
                runtime_context=runtime_context,
            )
            await send_prepared_job(device, connection, job, reporter=reporter)
        finally:
            await connection.disconnect()

    asyncio.run(run())
    return 0


def paper_motion_bluetooth(
    args: argparse.Namespace,
    action: str,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()

    async def run() -> None:
        device = await _resolve_bluetooth_device(args, catalog)
        _debug_resolved_device(reporter, device, action=action)
        job = build_paper_motion_job(device, action)
        connection = await BleakBluetoothConnector(reporter=reporter).connect(device)
        try:
            await connection.send(job)
        finally:
            try:
                await connection.disconnect()
            except Exception as exc:
                reporter.debug(short="Bluetooth", detail=f"Disconnect cleanup failed: {exc}")

    asyncio.run(run())
    return 0


def paper_motion_serial(args: argparse.Namespace, action: str, reporter: reporting.Reporter) -> int:
    catalog = PrinterCatalog.load()
    device = _resolve_serial_device(args, catalog)
    job = build_paper_motion_job(device, action)

    async def run() -> None:
        connection = await SerialConnector(reporter=reporter).connect(device)
        try:
            await connection.send(job)
        finally:
            await connection.disconnect()

    asyncio.run(run())
    return 0


def _build_cli_reporter(verbose: bool) -> reporting.Reporter:
    levels = {"warning", "error"}
    if verbose:
        levels.add("debug")
    return reporting.Reporter([reporting.StderrSink(levels=levels)])


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    reporter = _build_cli_reporter(args.verbose)
    emit_startup_warnings(reporter)
    if args.list_profiles:
        return list_profiles()
    if args.scan:
        return scan_devices(reporter)
    if args.export_profile_config:
        if (
            args.path
            or args.text is not None
            or args.feed
            or args.retract
            or args.bluetooth
            or args.serial
            or args.device_config
            or args.export_device_config
            or args.debug_profile
            or args.debug_dump_protocol_job
            or args.debug_image_encoding
        ):
            reporter.error(
                detail=(
                    "Provide only --export-profile-config KEY PATH when exporting a profile config. "
                    "Do not combine it with printing, transport, or other config flags. Use --help for usage."
                )
            )
            return 2
        try:
            return export_profile_config(args, reporter)
        except Exception as exc:
            reporter.error(detail=str(exc), exc=exc)
            return 2
    if args.export_device_config:
        if (
            args.path
            or args.text is not None
            or args.feed
            or args.retract
            or args.debug_profile
            or args.debug_dump_protocol_job
            or args.debug_image_encoding
        ):
            reporter.error(
                detail=(
                    "Provide either --export-device-config or a file path/--text/"
                    "--feed/--retract, not both. Use --help for usage."
                )
            )
            return 2
        try:
            return export_device_config(args, reporter)
        except Exception as exc:
            reporter.error(detail=str(exc), exc=exc)
            return 2
    if args.debug_dump_protocol_job:
        if args.feed or args.retract or args.serial:
            reporter.error(
                detail=(
                    "Provide --debug-dump-protocol-job with a printable file path or --text, "
                    "not with --feed/--retract or --serial. Use --debug-profile, --device-config, "
                    "or --bluetooth to select the offline device."
                )
            )
            return 2
        if args.path and args.text is not None:
            reporter.error(detail="Provide either a file path or --text, not both. Use --help for usage.")
            return 2
        if not args.path and args.text is None:
            reporter.error(
                detail="Missing file path or --text for --debug-dump-protocol-job. Use --help for usage."
            )
            return 2
        try:
            return debug_dump_protocol_job(args, reporter)
        except Exception as exc:
            reporter.error(detail=str(exc), exc=exc)
            return 2
    if args.debug_profile:
        reporter.error(
            detail="--debug-profile is only valid with --debug-dump-protocol-job. Use --help for usage."
        )
        return 2
    action = _resolve_paper_motion_action(args)
    if action and (args.path or args.text is not None):
        reporter.error(
            detail="Provide either --feed/--retract or a file path/--text, not both. Use --help for usage."
        )
        return 2
    if action and args.debug_image_encoding:
        reporter.error(
            detail="--debug-image-encoding is only valid for print jobs, not --feed/--retract."
        )
        return 2
    if args.path and args.text is not None:
        reporter.error(detail="Provide either a file path or --text, not both. Use --help for usage.")
        return 2
    if not action and not args.path and args.text is None:
        reporter.error(detail="Missing file path, --text, or a paper motion option. Use --help for usage.")
        return 2
    try:
        if action:
            if args.serial:
                return paper_motion_serial(args, action, reporter)
            return paper_motion_bluetooth(args, action, reporter)
        if args.serial:
            return print_serial(args, reporter)
        return print_bluetooth(args, reporter)
    except Exception as exc:
        reporter.error(detail=str(exc), exc=exc)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
