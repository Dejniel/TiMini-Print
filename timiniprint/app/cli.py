from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from typing import Optional

from ..devices import DeviceResolver, PrinterModel, PrinterModelRegistry
from ..transport.bluetooth import SppBackend
from ..transport.serial import SerialTransport
from .diagnostics import emit_startup_warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TiMini Print: Bluetooth printing for TiMini-compatible thermal printers."
    )
    parser.add_argument("path", nargs="?", help="File to print (.png/.jpg/.pdf/.txt)")
    parser.add_argument("--bluetooth", help="Bluetooth name or address (default: first supported printer)")
    parser.add_argument("--serial", metavar="PATH", help="Serial port path to bypass Bluetooth (e.g. /dev/rfcomm0)")
    parser.add_argument("--model", help="Printer model number (required for --serial)")
    parser.add_argument("--scan", action="store_true", help="List nearby supported printers and exit")
    parser.add_argument("--list-models", action="store_true", help="List known printer models and exit")
    parser.add_argument("--text", metavar="TEXT", help="Print raw text instead of a file path")
    parser.add_argument(
        "--text-font",
        metavar="PATH",
        help="Path to a .ttf/.otf font used for text rendering (default: monospace bold)",
    )
    parser.add_argument(
        "--text-columns",
        type=int,
        metavar="N",
        help="Target number of characters per line for text rendering",
    )
    parser.add_argument(
        "--text-hard-wrap",
        action="store_true",
        help="Disable whitespace word wrapping (enable hard-wrap by width) for text rendering (.txt or --text)",
    )
    parser.add_argument("--darkness", type=int, choices=range(1, 6), help="Print darkness (1-5)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--force-text-mode", action="store_true", help="Force printer protocol text mode")
    mode_group.add_argument("--force-image-mode", action="store_true", help="Force printer protocol image mode")
    motion_group = parser.add_mutually_exclusive_group()
    motion_group.add_argument("--feed", action="store_true", help="Advance paper")
    motion_group.add_argument("--retract", action="store_true", help="Retract paper")
    parser.epilog = "If any CLI options/arguments are provided, the GUI will not be launched."
    return parser.parse_args()


def list_models() -> int:
    registry = PrinterModelRegistry.load()
    for model in registry.models:
        print(model.model_no)
    return 0


def scan_devices() -> int:
    async def run() -> None:
        registry = PrinterModelRegistry.load()
        resolver = DeviceResolver(registry)
        devices = await SppBackend.scan()
        devices = resolver.filter_printer_devices(devices)
        for device in devices:
            name = device.name or ""
            status = " [unpaired]" if device.paired is False else ""
            if name:
                print(f"{name} ({device.address}){status}")
            else:
                print(f"{device.address}{status}")

    asyncio.run(run())
    return 0


def launch_gui() -> int:
    from .gui import TiMiniPrintGUI

    app = TiMiniPrintGUI()
    app.mainloop()
    return 0


def build_print_data(
    model: PrinterModel,
    path: Optional[str],
    text_mode: Optional[bool] = None,
    blackening: Optional[int] = None,
    text_input: Optional[str] = None,
    text_font: Optional[str] = None,
    text_columns: Optional[int] = None,
    text_wrap: bool = True,
) -> bytes:
    from ..printing import PrintJobBuilder, PrintSettings

    settings = PrintSettings(
        text_mode=text_mode,
        text_font=text_font,
        text_columns=text_columns,
        text_wrap=text_wrap,
    )
    if blackening is not None:
        settings.blackening = blackening
    builder = PrintJobBuilder(model, settings)
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


def build_paper_motion_data(model: PrinterModel, action: str) -> bytes:
    from ..protocol import advance_paper_cmd, retract_paper_cmd

    if action == "feed":
        return advance_paper_cmd(model.dev_dpi, model.new_format)
    if action == "retract":
        return retract_paper_cmd(model.dev_dpi, model.new_format)
    raise ValueError(f"Unknown paper motion action: {action}")


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
    return not args.text_no_wrap


def _resolve_paper_motion_action(args: argparse.Namespace) -> Optional[str]:
    if args.feed:
        return "feed"
    if args.retract:
        return "retract"
    return None


def _warn_alias_usage(match, device) -> None:
    if not match.used_alias:
        return
    name = device.name or "unknown"
    address = device.address or "unknown"
    print(
        "Warning: detected printer via alias (name: "
        f"{name}, address: {address}). Using standard profile settings for "
        f"{match.model.model_no}. If you can, please help improve the model "
        "parameters and share details with the project.",
        file=sys.stderr,
    )


def print_bluetooth(args: argparse.Namespace) -> int:
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)

    async def run() -> None:
        device = await resolver.resolve_printer_device(args.bluetooth)
        match = resolver.resolve_model_with_origin(device.name or "", args.model, device.address)
        _warn_alias_usage(match, device)
        model = match.model
        data = build_print_data(
            model,
            args.path,
            _resolve_text_mode(args),
            _resolve_blackening(args),
            _resolve_text_input(args),
            _resolve_text_font(args),
            _resolve_text_columns(args),
            _resolve_text_wrap(args),
        )
        backend = SppBackend()
        await backend.connect(device.address)
        await backend.write(data, model.img_mtu or 180, model.interval_ms or 4)
        await backend.disconnect()

    asyncio.run(run())
    return 0


def print_serial(args: argparse.Namespace) -> int:
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)
    model = resolver.require_model(args.model)
    data = build_print_data(
        model,
        args.path,
        _resolve_text_mode(args),
        _resolve_blackening(args),
        _resolve_text_input(args),
        _resolve_text_font(args),
        _resolve_text_columns(args),
        _resolve_text_wrap(args),
    )

    async def run() -> None:
        transport = SerialTransport(args.serial)
        await transport.write(data, model.img_mtu or 180, model.interval_ms or 4)

    asyncio.run(run())
    return 0


def paper_motion_bluetooth(args: argparse.Namespace, action: str) -> int:
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)

    async def run() -> None:
        device = await resolver.resolve_printer_device(args.bluetooth)
        match = resolver.resolve_model_with_origin(device.name or "", args.model, device.address)
        _warn_alias_usage(match, device)
        model = match.model
        data = build_paper_motion_data(model, action)
        backend = SppBackend()
        await backend.connect(device.address)
        await backend.write(data, model.img_mtu or 180, model.interval_ms or 4)
        await backend.disconnect()

    asyncio.run(run())
    return 0


def paper_motion_serial(args: argparse.Namespace, action: str) -> int:
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)
    model = resolver.require_model(args.model)
    data = build_paper_motion_data(model, action)

    async def run() -> None:
        transport = SerialTransport(args.serial)
        await transport.write(data, model.img_mtu or 180, model.interval_ms or 4)

    asyncio.run(run())
    return 0


def main() -> int:
    emit_startup_warnings()
    if len(sys.argv) == 1:
        return launch_gui()
    args = parse_args()
    if args.list_models:
        return list_models()
    if args.scan:
        return scan_devices()
    action = _resolve_paper_motion_action(args)
    if action and (args.path or args.text is not None):
        print(
            "Provide either --feed/--retract or a file path/--text, not both. Use --help for usage.",
            file=sys.stderr,
        )
        return 2
    if args.path and args.text is not None:
        print("Provide either a file path or --text, not both. Use --help for usage.", file=sys.stderr)
        return 2
    if not action and not args.path and args.text is None:
        print("Missing file path, --text, or a paper motion option. Use --help for usage.", file=sys.stderr)
        return 2
    try:
        if action:
            if args.serial:
                return paper_motion_serial(args, action)
            return paper_motion_bluetooth(args, action)
        if args.serial:
            return print_serial(args)
        return print_bluetooth(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
