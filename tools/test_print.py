#!/usr/bin/env python3
"""TiMini Print test pattern using the app CLI.

This script generates temporary images and prints them via timiniprint.app.cli
to exercise the normal application pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from typing import List, Optional

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
from timiniprint.devices import DeviceResolver, PrinterModelRegistry
from timiniprint.rendering.fonts import find_monospace_bold_font, load_font
from timiniprint.transport.bluetooth import SppBackend
from timiniprint.transport.serial import SerialTransport

DEFAULT_WIDTH = 384
MARGIN_LINE_THICKNESS = 20
POWER_HEADER_TEXT = "PRINT POWER TEST"
RETRACT_HEADER_TEXT = "RETRACT TEST"
RETRACT_BLOCK_HEIGHT = 20
RETRACT_LEFT_OFFSET = RETRACT_BLOCK_HEIGHT // 2
RETRACT_RIGHT_OFFSET = (-RETRACT_BLOCK_HEIGHT // 2) +1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TiMini Print: test pattern via app pipeline")
    parser.add_argument("--bluetooth", help="Bluetooth name or address to use")
    parser.add_argument("--serial", metavar="PATH", help="Serial port path to bypass Bluetooth")
    parser.add_argument("--model", help="Printer model number (required for --serial)")
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


def _resolve_width(args: argparse.Namespace) -> int:
    if args.model:
        registry = PrinterModelRegistry.load()
        model = registry.get(args.model)
        if model:
            return max(32, model.width)
        print(f"Warning: unknown model '{args.model}', using default width.", file=sys.stderr)
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
    draw.rectangle(
        [block_x, y, block_x + block_w - 1, y + RETRACT_BLOCK_HEIGHT - 1],
        fill=0,
    )
    draw.text(
        (block_x + max(0, (block_w - label_w) // 2), y + max(0, (RETRACT_BLOCK_HEIGHT - label_h) // 2)),
        "L" if side.upper() == "L" else "R",
        font=font,
        fill=255,
    )
    return img


def _build_print_data(model, path: str, darkness: int) -> bytes:
    return timini_cli.build_print_data(
        model,
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


def _build_retract_data(model) -> bytes:
    return timini_cli.build_paper_motion_data(model, "retract")


def _build_sequence(
    model,
    margin_path: str,
    power_paths: List[str],
    retract_left_path: str,
    retract_right_path: str,
) -> List[bytes]:
    sequence: List[bytes] = []
    retract_data = _build_retract_data(model)

    sequence.append(_build_print_data(model, margin_path, darkness=3))
    sequence.extend([retract_data, retract_data])

    for level, path in enumerate(power_paths, 1):
        sequence.append(_build_print_data(model, path, darkness=level))
        sequence.extend([retract_data, retract_data])

    sequence.append(_build_print_data(model, retract_left_path, darkness=3))
    sequence.extend([retract_data, retract_data, retract_data])
    sequence.append(_build_print_data(model, retract_right_path, darkness=3))
    return sequence


async def _run() -> int:
    args = parse_args()
    if args.serial and not args.model:
        print("Error: --model is required with --serial.", file=sys.stderr)
        return 2

    reporter = reporting.Reporter([reporting.StderrSink()])
    emit_startup_warnings(reporter)

    width = _resolve_width(args)
    font = _load_font(14)
    header_font = _load_font(18)

    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)
    device = None
    if args.serial:
        model = resolver.require_model(args.model)
    else:
        device = await resolver.resolve_printer_device(args.bluetooth)
        match = resolver.resolve_model_with_origin(device.name or "", args.model, device.address)
        timini_cli._warn_alias_usage(match, device, reporter)
        model = match.model

    with tempfile.TemporaryDirectory(prefix="timiniprint-test-") as tmpdir:
        margin_path = os.path.join(tmpdir, "margin.png")
        build_margin_image(width, header_font).save(margin_path)

        power_paths = []
        for level in range(1, 6):
            path = os.path.join(tmpdir, f"power_{level}.png")
            include_header = level == 1
            img = build_power_bar_image(width, level, font, header_font, include_header)
            img.save(path)
            power_paths.append(path)

        retract_left_path = os.path.join(tmpdir, "retract_left.png")
        retract_right_path = os.path.join(tmpdir, "retract_right.png")
        build_retract_image(
            width,
            "L",
            font,
            header_font,
            offset_y=RETRACT_LEFT_OFFSET,
            include_header=True,
        ).save(retract_left_path)
        build_retract_image(
            width,
            "R",
            font,
            header_font,
            offset_y=RETRACT_RIGHT_OFFSET,
            include_header=False,
        ).save(retract_right_path)

        sequence = _build_sequence(model, margin_path, power_paths, retract_left_path, retract_right_path)
        chunk_size = model.img_mtu or 180
        interval_ms = model.interval_ms or 4

        if args.serial:
            payload = b"".join(sequence)
            transport = SerialTransport(args.serial)
            await transport.write(payload, chunk_size, interval_ms)
        else:
            backend = SppBackend(reporter=reporter)
            await backend.connect(device, pairing_hint=device.paired is False)
            try:
                for data in sequence:
                    await backend.write(data, chunk_size, interval_ms)
            finally:
                await backend.disconnect()

    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(0)
