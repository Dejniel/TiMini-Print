#!/usr/bin/env python3
"""TiMini Print test pattern using the public object API."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from typing import List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    print("Error: Pillow (PIL) is required for this test script.", file=sys.stderr)
    raise SystemExit(2)

from timiniprint import reporting
from timiniprint.app import cli as timini_cli
from timiniprint.app.diagnostics import emit_startup_warnings
from timiniprint.devices import PrinterCatalog, PrinterDevice, SerialTarget
from timiniprint.protocol import PrinterProtocol, ProtocolJob
from timiniprint.rendering.fonts import find_monospace_bold_font, load_font
from timiniprint.transport.bluetooth import BleakBluetoothConnector, BluetoothDiscovery
from timiniprint.transport.serial import SerialConnector

DEFAULT_WIDTH = 384
MARGIN_LINE_THICKNESS = 20
POWER_HEADER_TEXT = "PRINT POWER TEST"
RETRACT_HEADER_TEXT = "RETRACT TEST"
RETRACT_BLOCK_HEIGHT = 20
RETRACT_LEFT_OFFSET = RETRACT_BLOCK_HEIGHT // 2
RETRACT_RIGHT_OFFSET = (-RETRACT_BLOCK_HEIGHT // 2) + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TiMini Print: test pattern via app pipeline")
    parser.add_argument("--bluetooth", help="Bluetooth name or address to use")
    parser.add_argument("--serial", metavar="PATH", help="Serial port path to bypass Bluetooth")
    parser.add_argument(
        "--printer-config",
        metavar="KEY_OR_PATH",
        help="Known printer model key/name or printer config JSON path",
    )
    return parser.parse_args()


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        font_path = find_monospace_bold_font()
        return load_font(font_path, size)
    except Exception:
        return ImageFont.load_default()


def _text_size(text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if hasattr(font, "getbbox"):
        left, top, right, bottom = font.getbbox(text)
        return right - left, bottom - top
    if hasattr(font, "getsize"):
        return font.getsize(text)
    draw = ImageDraw.Draw(Image.new("L", (1, 1), 255))
    return draw.textsize(text, font=font)


def _load_printer_config(catalog: PrinterCatalog, value: str | None) -> dict | None:
    if not value:
        return None
    try:
        catalog.device_from_key(value)
        return None
    except RuntimeError:
        pass
    path = timini_cli._printer_config_path(value)
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Printer config JSON must contain an object at the top level")
    return raw


def _resolve_width(printer_config_value: str | None) -> int:
    catalog = PrinterCatalog.load()
    printer_config = _load_printer_config(catalog, printer_config_value)
    if printer_config:
        try:
            return max(
                32,
                catalog.device_from_printer_config(printer_config, transport_target=None).profile.width,
            )
        except RuntimeError as exc:
            print(f"Warning: invalid printer config width source: {exc}", file=sys.stderr)
            return DEFAULT_WIDTH
    profile_key = printer_config_value
    if profile_key:
        try:
            device = catalog.device_from_key(str(profile_key), transport_target=None)
        except RuntimeError:
            print(f"Warning: unknown printer config key '{profile_key}', using default width.", file=sys.stderr)
        else:
            return max(32, device.profile.width)
    return DEFAULT_WIDTH


def build_margin_image(width: int, header_font: ImageFont.ImageFont) -> Image.Image:
    label = "MARGINS TEST"
    label_w, label_h = _text_size(label, header_font)
    img = Image.new("L", (width, max(56, label_h + 16)), 255)
    draw = ImageDraw.Draw(img)
    half = MARGIN_LINE_THICKNESS // 2
    left_x = 4 + half
    right_x = width - 1 - 4 - half
    draw.line([(left_x, 0), (left_x, img.height - 1)], fill=0, width=MARGIN_LINE_THICKNESS)
    draw.line([(right_x, 0), (right_x, img.height - 1)], fill=0, width=MARGIN_LINE_THICKNESS)
    draw.text((max(4, (width - label_w) // 2), 6), label, font=header_font, fill=0)
    return img


def build_power_bar_image(
    width: int,
    level: int,
    font: ImageFont.ImageFont,
    header_font: ImageFont.ImageFont,
    include_header: bool,
) -> Image.Image:
    header_h = (_text_size(POWER_HEADER_TEXT, header_font)[1] + 6) if include_header else 0
    bar_h = 18
    label_text = f"D{level}"
    label_w, label_h = _text_size(label_text, font)
    img = Image.new("L", (width, header_h + bar_h + 8), 255)
    draw = ImageDraw.Draw(img)
    y = 4
    if include_header:
        header_w, header_text_h = _text_size(POWER_HEADER_TEXT, header_font)
        draw.text((max(4, (width - header_w) // 2), y), POWER_HEADER_TEXT, font=header_font, fill=0)
        y += header_text_h + 6
    label_y = y + max(0, (bar_h - label_h) // 2)
    draw.text((10, label_y), label_text, font=font, fill=0)
    bar_x1 = min(width - 16, 10 + label_w + 6)
    bar_x2 = width - 10
    if bar_x1 < bar_x2:
        draw.rectangle([bar_x1, y, bar_x2, y + bar_h - 1], fill=0)
    return img


def build_retract_image(
    width: int,
    side: str,
    font: ImageFont.ImageFont,
    header_font: ImageFont.ImageFont,
    offset_y: int = 0,
    include_header: bool = True,
) -> Image.Image:
    header_w, header_text_h = _text_size(RETRACT_HEADER_TEXT, header_font)
    label_w, label_h = _text_size(side, font)
    block_w = max(1, width // 2)
    base_top = 4 + header_text_h + 6
    height = max(base_top + RETRACT_BLOCK_HEIGHT + 12, base_top + offset_y + RETRACT_BLOCK_HEIGHT + 12)
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    if include_header:
        draw.text((max(4, (width - header_w) // 2), 4), RETRACT_HEADER_TEXT, font=header_font, fill=0)
    y = max(0, base_top + offset_y)
    block_x = 0 if side.upper() == "L" else width - block_w
    draw.rectangle([block_x, y, block_x + block_w - 1, y + RETRACT_BLOCK_HEIGHT - 1], fill=0)
    draw.text(
        (block_x + max(0, (block_w - label_w) // 2), y + max(0, (RETRACT_BLOCK_HEIGHT - label_h) // 2)),
        "L" if side.upper() == "L" else "R",
        font=font,
        fill=255,
    )
    return img


def _build_print_job(device: PrinterDevice, path: str, darkness: int) -> ProtocolJob:
    return timini_cli.build_print_job(
        device,
        path,
        text_mode=None,
        blackening=darkness,
        text_input=None,
        text_font=None,
        text_columns=None,
        text_wrap=True,
        trim_side_margins=False,
        trim_top_bottom_margins=False,
        pdf_pages=None,
        pdf_page_gap_mm=0,
    )


def _build_retract_job(device: PrinterDevice) -> ProtocolJob:
    return PrinterProtocol(device).build_paper_motion("retract")


def _build_sequence(
    device: PrinterDevice,
    margin_path: str,
    power_paths: List[str],
    retract_left_path: str,
    retract_right_path: str,
) -> List[ProtocolJob]:
    sequence: List[ProtocolJob] = []
    retract_job = _build_retract_job(device)

    sequence.append(_build_print_job(device, margin_path, darkness=3))
    sequence.extend([retract_job, retract_job])

    for level, path in enumerate(power_paths, 1):
        sequence.append(_build_print_job(device, path, darkness=level))
        sequence.extend([retract_job, retract_job])

    sequence.append(_build_print_job(device, retract_left_path, darkness=3))
    sequence.extend([retract_job, retract_job, retract_job])
    sequence.append(_build_print_job(device, retract_right_path, darkness=3))
    return sequence


async def _resolve_device(args: argparse.Namespace) -> PrinterDevice:
    catalog = PrinterCatalog.load()
    printer_config = _load_printer_config(catalog, args.printer_config)
    if args.serial:
        if args.printer_config is None:
            raise RuntimeError("--printer-config is required with --serial.")
        if printer_config is not None:
            return catalog.device_from_printer_config(
                printer_config,
                transport_target=SerialTarget(args.serial),
            )
        return catalog.device_from_key(
            args.printer_config,
            transport_target=SerialTarget(args.serial),
        )

    discovery = BluetoothDiscovery(catalog)
    if printer_config is None:
        if args.printer_config:
            if args.bluetooth:
                detected = await discovery.resolve_transport_target(args.bluetooth)
                return catalog.device_from_key(
                    args.printer_config,
                    display_name=detected.display_name,
                    transport_target=detected.transport_target,
                )
            return catalog.device_from_key(args.printer_config)
        return await discovery.resolve_device(args.bluetooth)
    if args.bluetooth:
        detected = await discovery.resolve_transport_target(args.bluetooth)
        return catalog.device_from_printer_config(
            printer_config,
            transport_target=detected.transport_target,
            display_name=detected.display_name,
        )
    return catalog.device_from_printer_config(printer_config)


async def _run() -> int:
    args = parse_args()
    reporter = reporting.Reporter([reporting.StderrSink()])
    emit_startup_warnings(reporter)

    width = _resolve_width(args.printer_config)
    font = _load_font(14)
    header_font = _load_font(18)
    device = await _resolve_device(args)

    with tempfile.TemporaryDirectory(prefix="timiniprint-test-") as tmpdir:
        margin_path = os.path.join(tmpdir, "margin.png")
        build_margin_image(width, header_font).save(margin_path)

        power_paths = []
        for level in range(1, 6):
            path = os.path.join(tmpdir, f"power_{level}.png")
            include_header = level == 1
            build_power_bar_image(width, level, font, header_font, include_header).save(path)
            power_paths.append(path)

        retract_left_path = os.path.join(tmpdir, "retract_left.png")
        retract_right_path = os.path.join(tmpdir, "retract_right.png")
        build_retract_image(width, "L", font, header_font, offset_y=RETRACT_LEFT_OFFSET, include_header=True).save(retract_left_path)
        build_retract_image(width, "R", font, header_font, offset_y=RETRACT_RIGHT_OFFSET, include_header=False).save(retract_right_path)

        sequence = _build_sequence(device, margin_path, power_paths, retract_left_path, retract_right_path)

        if args.serial:
            connection = await SerialConnector().connect(device)
        else:
            connection = await BleakBluetoothConnector(reporter=reporter).connect(device)
        try:
            for job in sequence:
                await connection.send(job)
        finally:
            await connection.disconnect()

    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(0)
