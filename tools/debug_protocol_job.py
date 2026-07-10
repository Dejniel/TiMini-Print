from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from timiniprint.app import cli  # noqa: E402
from timiniprint.devices import PrinterCatalog, PrinterDevice  # noqa: E402
from timiniprint.devices.printer_config import runtime_settings_from_parts  # noqa: E402
from timiniprint.printing.builder import PrintJobBuilder  # noqa: E402
from timiniprint.printing.debug_dump import build_protocol_packet_entries  # noqa: E402
from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PaperMode, PrinterProtocol, ProtocolJob  # noqa: E402
from timiniprint.protocol.families import get_protocol_behavior  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a diagnostic protocol job dump without connecting to a printer."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", metavar="KEY_OR_NAME", help="Known printer model key or public model name")
    source.add_argument("--profile", metavar="KEY", help="Raw profile key for low-level diagnostics")
    source.add_argument("--runtime-preset", metavar="KEY", help="Runtime preset key for low-level diagnostics")
    source.add_argument("--printer-config", metavar="PATH", help="Editable printer config JSON")
    source.add_argument("--bluetooth-name", metavar="NAME", help="Resolve a known model from a Bluetooth advertised name")
    parser.add_argument("path", nargs="?", help="File to render into a protocol job")
    parser.add_argument("--text", metavar="TEXT", help="Render text into a protocol job instead of a file")
    parser.add_argument("--out", required=True, metavar="PATH", help="Output JSON dump path")
    parser.add_argument("--image-encoding", choices=[encoding.value for encoding in ImageEncoding])
    parser.add_argument("--debug-row-markers", type=int, metavar="N")
    parser.add_argument("--force-text-mode", action="store_true")
    parser.add_argument("--force-image-mode", action="store_true")
    parser.add_argument("--darkness", type=int, choices=range(1, 6))
    parser.add_argument("--text-font", metavar="PATH")
    parser.add_argument("--text-columns", type=int, metavar="N")
    parser.add_argument("--text-hard-wrap", action="store_true")
    parser.add_argument("--pdf-pages", metavar="PAGES")
    parser.add_argument("--page-gap", type=int, metavar="MM")
    parser.add_argument("--no-trim-side-margins", action="store_false", dest="trim_side_margins")
    parser.add_argument("--no-trim-top-bottom-margins", action="store_false", dest="trim_top_bottom_margins")
    parser.add_argument("--paper-mode", choices=[mode.value for mode in PaperMode])
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.set_defaults(trim_side_margins=True, trim_top_bottom_margins=True)
    args = parser.parse_args(argv)
    if bool(args.path) + (args.text is not None) != 1:
        parser.error("Provide exactly one input: file path or --text")
    return args


def resolve_device(catalog: PrinterCatalog, args: argparse.Namespace) -> PrinterDevice:
    if args.model:
        return catalog.device_from_key(args.model)
    if args.profile:
        return catalog.device_from_profile(args.profile)
    if args.runtime_preset:
        return _debug_device_from_runtime_preset(catalog, args.runtime_preset)
    if args.printer_config:
        return catalog.device_from_printer_config(cli._load_printer_config(args.printer_config))
    device = catalog.detect_device(args.bluetooth_name)
    if device is None:
        raise RuntimeError(f"Unknown Bluetooth advertised name: {args.bluetooth_name}")
    return device


def _debug_device_from_runtime_preset(
    catalog: PrinterCatalog,
    profile_runtime_preset_key: str,
) -> PrinterDevice:
    for profile in catalog.profiles:
        for preset in profile.runtime_presets:
            if preset.key != profile_runtime_preset_key:
                continue
            return PrinterDevice(
                display_name=profile_runtime_preset_key,
                profile=profile,
                protocol_family=profile.protocol_default.type,
                protocol_variant=profile.protocol_default.packets_type,
                image_pipeline=profile.default_image_pipeline,
                runtime_settings=runtime_settings_from_parts(preset=preset),
                model_key=f"debug-runtime:{profile_runtime_preset_key}",
            )
    raise RuntimeError(f"Unknown runtime preset '{profile_runtime_preset_key}'")


def _image_encoding_override(args: argparse.Namespace) -> ImageEncoding | None:
    if not args.image_encoding:
        return None
    return ImageEncoding(args.image_encoding)


def _paper_mode(args: argparse.Namespace) -> PaperMode | None:
    if not args.paper_mode:
        return None
    return PaperMode(args.paper_mode)


def build_print_job(
    device: PrinterDevice,
    path: str | None,
    *,
    text_mode: bool | None,
    blackening: int | None,
    text_input: str | None,
    text_font: str | None,
    text_columns: int | None,
    text_wrap: bool,
    trim_side_margins: bool,
    trim_top_bottom_margins: bool,
    pdf_pages: str | None,
    page_gap_mm: int,
    paper_mode: PaperMode | None,
    image_encoding_override: ImageEncoding | None,
    debug_row_markers_interval: int | None,
    reporter: reporting.Reporter | None,
) -> ProtocolJob:
    paper_preset_key = None if paper_mode is None else device.profile.paper_preset_for_mode(paper_mode).key
    settings = cli.create_print_settings(
        text_mode=text_mode,
        blackening=blackening,
        text_font=text_font,
        text_columns=text_columns,
        text_wrap=text_wrap,
        trim_side_margins=trim_side_margins,
        trim_top_bottom_margins=trim_top_bottom_margins,
        pdf_pages=pdf_pages,
        page_gap_mm=page_gap_mm,
        paper_preset_key=paper_preset_key,
        image_encoding_override=image_encoding_override,
        debug_row_markers_interval=debug_row_markers_interval,
    )
    builder = PrintJobBuilder(device, settings=settings, reporter=reporter)
    if text_input is None:
        if path is None:
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


def build_protocol_job_debug_dump(
    device: PrinterDevice,
    job: ProtocolJob,
    *,
    settings: Mapping[str, object],
    effective_image_pipeline: ImagePipelineConfig | None = None,
) -> dict[str, object]:
    runtime_settings = device.runtime_settings
    runtime_preset = None if runtime_settings is None else runtime_settings.preset
    runtime_capabilities = None if runtime_settings is None else runtime_settings.capabilities
    transport = get_protocol_behavior(device.protocol_family).transport
    return {
        "schema": "timiniprint/debug-protocol-job/v1",
        "diagnostic_only": True,
        "device": {
            "display_name": device.display_name,
            "profile_key": device.profile_key,
            "protocol_family": device.protocol_family.value,
            "protocol_variant": device.protocol_variant,
            "image_pipeline": {
                "encoding": device.image_pipeline.encoding.value,
                "formats": [pixel_format.value for pixel_format in device.image_pipeline.formats],
            },
            "runtime_control_algorithm": (
                None if runtime_settings is None else runtime_settings.control_algorithm
            ),
            "profile_runtime_preset_key": None if runtime_preset is None else runtime_preset.key,
            "runtime_density": (
                None
                if runtime_preset is None or runtime_preset.density is None
                else _mode_level_debug_entry(runtime_preset.density)
            ),
            "runtime_capabilities": {
                "d2_status": False if runtime_capabilities is None else runtime_capabilities.d2_status,
                "didian_status": (
                    False if runtime_capabilities is None else runtime_capabilities.didian_status
                ),
            },
            "model_key": device.model_key,
        },
        "settings": dict(settings),
        "transport": {
            "connect_packets": [packet.hex() for packet in transport.connect_packets],
            "connect_delay_ms": transport.connect_delay_ms,
            "standard_chunk_cap": transport.standard_chunk_cap,
            "standard_write_delay_ms": transport.standard_write_delay_ms,
            "write_without_response_payload_reserve": (
                transport.write_without_response_payload_reserve
            ),
        },
        "job": {
            "payload_bytes": len(job.payload),
            "payload_segments": len(job.payload_segments),
            "effective_image_pipeline": (
                None
                if effective_image_pipeline is None
                else _image_pipeline_debug_entry(effective_image_pipeline)
            ),
            "steps": [
                {
                    "label": step.label,
                    "operation": step.operation.value,
                    "bytes": len(step.data),
                    "expect": step.expect.value,
                }
                for step in job.steps
            ],
            "wait_for_completion": job.wait_for_completion,
        },
        "packets": build_protocol_packet_entries(device, job.payload),
        "payload_hex": job.payload.hex(),
    }


def _image_pipeline_debug_entry(pipeline: ImagePipelineConfig) -> dict[str, object]:
    return {
        "encoding": pipeline.encoding.value,
        "formats": [pixel_format.value for pixel_format in pipeline.formats],
    }


def _mode_level_debug_entry(profile) -> dict[str, dict[str, int]]:
    return {
        "image": {
            "low": profile.image.low,
            "middle": profile.image.middle,
            "high": profile.image.high,
        },
        "text": {
            "low": profile.text.low,
            "middle": profile.text.middle,
            "high": profile.text.high,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    catalog = PrinterCatalog.load()
    reporter = cli._build_cli_reporter(args.verbose)
    device = resolve_device(catalog, args)
    image_encoding_override = _image_encoding_override(args)
    effective_image_pipeline = PrinterProtocol(device).resolve_image_pipeline(
        image_encoding_override=image_encoding_override,
    )
    job = build_print_job(
        device,
        args.path,
        text_mode=cli._resolve_text_mode(args),
        blackening=cli._resolve_blackening(args),
        text_input=cli._resolve_text_input(args),
        text_font=cli._resolve_text_font(args),
        text_columns=cli._resolve_text_columns(args),
        text_wrap=cli._resolve_text_wrap(args),
        trim_side_margins=cli._resolve_trim_side_margins(args),
        trim_top_bottom_margins=cli._resolve_trim_top_bottom_margins(args),
        pdf_pages=cli._resolve_pdf_pages(args),
        page_gap_mm=cli._resolve_page_gap(args),
        paper_mode=_paper_mode(args),
        image_encoding_override=image_encoding_override,
        debug_row_markers_interval=args.debug_row_markers,
        reporter=reporter if args.verbose else None,
    )
    dump = build_protocol_job_debug_dump(
        device,
        job,
        settings={
            "darkness": args.darkness,
            "text_mode": cli._resolve_text_mode(args),
            "paper_mode": args.paper_mode,
            "image_encoding_override": args.image_encoding,
            "debug_row_markers": args.debug_row_markers,
        },
        effective_image_pipeline=effective_image_pipeline,
    )
    Path(args.out).write_text(json.dumps(dump, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
