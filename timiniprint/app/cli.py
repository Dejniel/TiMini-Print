from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Mapping
from typing import Optional, Sequence

from .. import __version__, reporting
from ..devices import BluetoothTarget, PrinterCatalog, PrinterDevice, SerialTarget
from ..printing.builder import PrintJobBuilder
from ..printing.runtime.base import PreparedRuntimeContext
from ..printing.runtime.prepare import prepare_connection_runtime
from ..printing.send import send_prepared_job
from ..printing.settings import PrintSettings
from ..protocol import ImageEncoding, PaperMode, PrinterProtocol, ProtocolJob
from ..transport.bluetooth import BluetoothDiscovery, BleakBluetoothConnector
from ..transport.bluetooth.types import DeviceTransport
from ..transport.serial import SerialConnector
from ..update_check import check_for_updates, should_check_for_updates
from .diagnostics import emit_startup_warnings

_TRANSPORT_UNSET = object()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TiMini Print: Bluetooth printing for TiMini-compatible thermal printers."
    )
    parser.add_argument("path", nargs="?", help="File to print (.png/.jpg/.pdf/.txt)")
    parser.add_argument("--bluetooth", help="Bluetooth name or address (default: first supported printer)")
    parser.add_argument("--serial", metavar="PATH", help="Serial port path to bypass Bluetooth (e.g. /dev/rfcomm0)")
    parser.add_argument(
        "--printer-config",
        metavar="KEY_OR_PATH",
        help="Known printer model key, public model name, or printer config JSON path used for manual overrides",
    )
    parser.add_argument(
        "--export-printer-config",
        nargs=2,
        metavar=("KEY", "PATH"),
        help="Write a fresh editable printer config JSON for a known printer model key or public model name and exit",
    )
    parser.add_argument(
        "--debug-row-markers",
        type=int,
        metavar="N",
        help="Debug only: add side row markers every N raster rows",
    )
    parser.add_argument("--scan", action="store_true", help="List nearby supported printers and exit")
    parser.add_argument("--list-models", action="store_true", help="List known printer model keys and public names and exit")
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


def list_models() -> int:
    catalog = PrinterCatalog.load()
    for model in sorted(catalog.models, key=lambda model: model.model_key):
        names = ", ".join(model.names)
        print(f"{model.model_key}: {names}")
    return 0


def emit_update_warning(reporter: reporting.Reporter) -> None:
    if not should_check_for_updates(source_builds=False):
        return
    try:
        result = check_for_updates()
    except Exception:
        return
    if result is None:
        return
    reporter.warning(
        short=f"Update available: {result.latest_version}",
        detail=(
            f"Update available: {result.latest_version} "
            f"(current: {result.current_version}). {result.release_url}"
        ),
    )


def emit_startup_debug(reporter: reporting.Reporter) -> None:
    reporter.debug(
        short="Startup",
        detail=(
            "Startup: "
            f"timiniprint_version={__version__} "
            f"platform={platform.platform()} "
            f"machine={platform.machine()} "
            f"python={platform.python_version()} "
            f"frozen={bool(getattr(sys, 'frozen', False))}"
        ),
    )


def _load_printer_config(path: str) -> Mapping[str, object]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Printer config JSON must contain an object at the top level")
    return raw


def _write_printer_config(path: str, printer_config: Mapping[str, object]) -> None:
    Path(path).write_text(
        json.dumps(dict(printer_config), indent=2) + "\n",
        encoding="utf-8",
    )


def _printer_config_path(value: str) -> Path | None:
    path = Path(value)
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"Printer config path '{value}' is not a file")
        return path
    if path.suffix.lower() == ".json" or len(path.parts) > 1:
        raise RuntimeError(f"Printer config file not found: {value}")
    return None


def _device_from_printer_config_arg(
    catalog: PrinterCatalog,
    value: str,
    *,
    transport_target=_TRANSPORT_UNSET,
    display_name: str | None = None,
) -> PrinterDevice:
    profile_transport_target = (
        None if transport_target is _TRANSPORT_UNSET else transport_target
    )
    try:
        return catalog.device_from_key(
            value,
            display_name=display_name,
            transport_target=profile_transport_target,
        )
    except RuntimeError as key_error:
        path = _printer_config_path(value)
        if path is None:
            raise key_error

    kwargs = {}
    if transport_target is not _TRANSPORT_UNSET:
        kwargs["transport_target"] = transport_target
    if display_name is not None:
        kwargs["display_name"] = display_name
    return catalog.device_from_printer_config(
        _load_printer_config(str(path)),
        **kwargs,
    )


def scan_devices(reporter: reporting.Reporter) -> int:
    async def run() -> None:
        catalog = PrinterCatalog.load()
        discovery = BluetoothDiscovery(catalog, reporter=reporter)
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
    debug_row_markers_interval: Optional[int] = None,
    reporter: reporting.Reporter | None = None,
) -> PrintJobBuilder:
    if debug_row_markers_interval is not None and debug_row_markers_interval <= 0:
        raise ValueError("Debug row marker interval must be positive")
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
        debug_row_markers_interval=debug_row_markers_interval,
    )
    if blackening is not None:
        settings.blackening = blackening
    return PrintJobBuilder(
        device,
        settings=settings,
        runtime_context=runtime_context,
        reporter=reporter,
    )


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
    debug_row_markers_interval: Optional[int] = None,
    reporter: reporting.Reporter | None = None,
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
        debug_row_markers_interval=debug_row_markers_interval,
        reporter=reporter,
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
    reporter: reporting.Reporter | None = None,
) -> PrinterDevice:
    discovery = BluetoothDiscovery(catalog, reporter=reporter)
    if not args.printer_config:
        return await discovery.resolve_device(args.bluetooth)
    if args.bluetooth:
        detected = await discovery.resolve_transport_target(args.bluetooth)
        return _device_from_printer_config_arg(
            catalog,
            args.printer_config,
            transport_target=detected.transport_target,
            display_name=detected.display_name,
        )
    device = _device_from_printer_config_arg(catalog, args.printer_config)
    if isinstance(device.transport_target, BluetoothTarget):
        return device
    if device.transport_target is not None:
        raise RuntimeError(
            "Bluetooth printing with --printer-config cannot use a non-Bluetooth "
            "transport target saved in the printer config"
        )
    detected = await discovery.resolve_transport_target(None)
    return _device_from_printer_config_arg(
        catalog,
        args.printer_config,
        transport_target=detected.transport_target,
        display_name=detected.display_name,
    )


def _resolve_serial_device(
    args: argparse.Namespace,
    catalog: PrinterCatalog,
) -> PrinterDevice:
    if not args.printer_config:
        raise RuntimeError(
            "Serial printing requires --printer-config "
            "(use a printer model key/name or export one first with --export-printer-config)"
        )
    return _device_from_printer_config_arg(
        catalog,
        args.printer_config,
        transport_target=SerialTarget(args.serial),
    )


def _field_value(value) -> object:
    return value.value if hasattr(value, "value") else value


def _debug_resolved_device(
    reporter: reporting.Reporter,
    device: PrinterDevice,
    *,
    action: str,
) -> None:
    runtime_settings = device.runtime_settings
    runtime_preset = None if runtime_settings is None else runtime_settings.preset
    origin_app_packages = device.origin_app_packages
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
            f"runtime={getattr(runtime_settings, 'control_algorithm', None) or '<none>'} "
            f"runtime_preset={getattr(runtime_preset, 'key', None) or '<none>'} "
            f"model={device.model_key or '<none>'} "
            f"origin_app_packages={','.join(origin_app_packages) or '<none>'} "
            f"use_spp={device.profile.use_spp}"
        ),
    )


def export_printer_config(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()
    key, out_path = args.export_printer_config
    device = catalog.device_from_key(key)
    _write_printer_config(
        out_path,
        catalog.serialize_printer_config(device),
    )
    reporter.debug(
        short="Printer config",
        detail=(
            "Exported printer config: "
            f"path={out_path} "
            f"profile={device.profile_key} "
            f"family={device.protocol_family.value}"
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


def _print_input_count(args: argparse.Namespace) -> int:
    return sum(
        1
        for has_input in (
            bool(args.path),
            args.text is not None,
        )
        if has_input
    )


def print_bluetooth(
    args: argparse.Namespace,
    reporter: reporting.Reporter,
) -> int:
    catalog = PrinterCatalog.load()

    async def run() -> None:
        device = await _resolve_bluetooth_device(
            args,
            catalog,
            reporter=reporter if args.verbose else None,
        )
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
                runtime_context=runtime_context,
                debug_row_markers_interval=args.debug_row_markers,
                reporter=reporter if args.verbose else None,
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
                runtime_context=runtime_context,
                debug_row_markers_interval=args.debug_row_markers,
                reporter=reporter if args.verbose else None,
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
        device = await _resolve_bluetooth_device(
            args,
            catalog,
            reporter=reporter if args.verbose else None,
        )
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
    emit_startup_debug(reporter)
    emit_startup_warnings(reporter)
    emit_update_warning(reporter)
    if args.list_models:
        return list_models()
    if args.scan:
        return scan_devices(reporter)
    if args.export_printer_config:
        if (
            args.path
            or args.text is not None
            or args.feed
            or args.retract
            or args.bluetooth
            or args.serial
            or args.printer_config
            or args.debug_row_markers is not None
        ):
            reporter.error(
                detail=(
                    "Provide only --export-printer-config KEY PATH when exporting a printer config. "
                    "Do not combine it with printing, transport, or other printer config flags. Use --help for usage."
                )
            )
            return 2
        try:
            return export_printer_config(args, reporter)
        except Exception as exc:
            reporter.error(detail=str(exc), exc=exc)
            return 2
    action = _resolve_paper_motion_action(args)
    if action and (args.path or args.text is not None):
        reporter.error(
            detail="Provide either --feed/--retract or a file path/--text, not both. Use --help for usage."
        )
        return 2
    if action and args.debug_row_markers is not None:
        reporter.error(
            detail="Debug print modifiers are only valid for print jobs, not --feed/--retract."
        )
        return 2
    print_inputs = _print_input_count(args)
    if print_inputs > 1:
        reporter.error(
            detail=(
                "Provide either a file path or --text, not both. Use --help for usage."
            )
        )
        return 2
    if not action and print_inputs == 0:
        reporter.error(
            detail="Missing file path, --text, or a paper motion option. Use --help for usage."
        )
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
