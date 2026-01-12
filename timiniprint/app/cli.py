from __future__ import annotations

import argparse
import asyncio
import sys
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
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--text-mode", action="store_true", help="Force text mode printing")
    mode_group.add_argument("--image-mode", action="store_true", help="Force image mode printing")
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


def build_print_data(model: PrinterModel, path: str, text_mode: Optional[bool] = None) -> bytes:
    from ..printing import PrintJobBuilder, PrintSettings

    settings = PrintSettings(text_mode=text_mode)
    builder = PrintJobBuilder(model, settings)
    return builder.build_from_file(path)


def _resolve_text_mode(args: argparse.Namespace) -> Optional[bool]:
    if args.text_mode:
        return True
    if args.image_mode:
        return False
    return None


def print_bluetooth(args: argparse.Namespace) -> int:
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)

    async def run() -> None:
        device = await resolver.resolve_printer_device(args.bluetooth)
        model = resolver.resolve_model(device.name or "", args.model)
        data = build_print_data(model, args.path, _resolve_text_mode(args))
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
    data = build_print_data(model, args.path, _resolve_text_mode(args))

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
    if not args.path:
        print("Missing file path. Use --help for usage.", file=sys.stderr)
        return 2
    try:
        if args.serial:
            return print_serial(args)
        return print_bluetooth(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
