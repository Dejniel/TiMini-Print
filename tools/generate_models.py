#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

CONSTANTS = {
    "OS2WindowsMetricsTable.WEIGHT_CLASS_LIGHT": 300,
    "OS2WindowsMetricsTable.WEIGHT_CLASS_MEDIUM": 500,
    "OS2WindowsMetricsTable.WEIGHT_CLASS_BOLD": 700,
    "OS2WindowsMetricsTable.WEIGHT_CLASS_EXTRA_BOLD": 800,
    "OS2WindowsMetricsTable.WEIGHT_CLASS_BLACK": 900,
    "Constants.CP_MAC_ROMAN": 10000,
    "Shape.MASTER_DPI": 576,
    "BannerConfig.LOOP_TIME": 3000,
    "AccessibilityNodeInfoCompat.EXTRA_DATA_TEXT_CHARACTER_LOCATION_ARG_MAX_LENGTH": 20000,
    "PDLayoutAttributeObject.GLYPH_ORIENTATION_VERTICAL_ZERO_DEGREES": "0",
}


def extract_args(text: str, start: int) -> str:
    depth = 1
    i = start
    in_str = False
    esc = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return text[start:i]
        i += 1
    raise ValueError("No closing paren found")


def split_args(arg_str: str) -> list[str]:
    args = []
    cur = []
    depth = 0
    in_str = False
    esc = False
    for ch in arg_str:
        if in_str:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
                cur.append(ch)
            elif ch in "([{":
                depth += 1
                cur.append(ch)
            elif ch in ")]}":
                depth -= 1
                cur.append(ch)
            elif ch == ',' and depth == 0:
                args.append(''.join(cur).strip())
                cur = []
            else:
                cur.append(ch)
    if cur:
        args.append(''.join(cur).strip())
    return args


def parse_token(tok: str):
    tok = tok.strip()
    if tok.startswith("(int) "):
        tok = tok[len("(int) ") :].strip()
    elif tok.startswith("(float) "):
        tok = tok[len("(float) ") :].strip()
    elif tok.startswith("(double) "):
        tok = tok[len("(double) ") :].strip()
    if tok in CONSTANTS:
        return CONSTANTS[tok]
    if tok == "true":
        return True
    if tok == "false":
        return False
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    if re.match(r"^-?\d+\.\d+[df]$", tok):
        return float(tok[:-1])
    if re.match(r"^-?\d+\.\d+$", tok):
        return float(tok)
    if re.match(r"^-?\d+$", tok):
        return int(tok)
    raise ValueError(f"Unknown token: {tok}")


def paper_preset_key(preset: dict) -> str:
    paper_width = preset["paper_width_px"]
    render_width = preset["render_width_px"]
    key = f"default_{render_width}r"
    if paper_width != render_width or preset.get("left_padding_px"):
        key += f"_{paper_width}p"
    if preset.get("left_padding_px"):
        key += f"_{preset['left_padding_px']}pl"
    return key


def source_left_padding_px(model_no: str, add_more_pix_num: int) -> int:
    if add_more_pix_num >= 0:
        return add_more_pix_num
    if model_no in {"X8-L", "X8-W"}:
        return 40
    return 64


def paper_preset_from_args(args: list[object]) -> dict[str, object]:
    model_no = str(args[0])
    size = int(args[2])
    paper_size = int(args[3])
    print_size = int(args[4])
    add_mor_pix, add_more_pix_num = paper_padding_flags_from_args(args)

    preset: dict[str, object] = {
        "key": "default",
        "label": "Default",
        "paper_width_px": max(print_size, paper_size),
        "render_width_px": paper_size,
    }
    if size == 8 and add_mor_pix:
        left_padding = source_left_padding_px(model_no, add_more_pix_num)
        preset["paper_width_px"] = paper_size + left_padding
        preset["left_padding_px"] = left_padding
    return preset


def paper_padding_flags_from_args(args: list[object]) -> tuple[bool, int]:
    # TinyPrint overloads store addMorPix/addMorePixNum near the constructor tail.
    # Keep this source-shaped so generated data does not infer padding from widths.
    rem = args[25:]
    if _matches(rem, bool, int, int, int, bool, int):
        return bool(rem[4]), int(rem[5])
    if _matches(rem, bool, int, int, bool, int):
        return bool(rem[3]), int(rem[4])
    if _matches(rem, bool, int, int, bool):
        return bool(rem[3]), -1
    if _matches(rem, bool, int, bool, bool):
        return bool(rem[2]), -1
    if _matches(rem, int, int, bool, bool):
        return bool(rem[2]), -1
    return True, -1


def _matches(values: list[object], *types: type) -> bool:
    if len(values) < len(types):
        return False
    return all(type(value) is expected for value, expected in zip(values, types))


def generate_models(text: str) -> tuple[list[dict], dict[str, dict]]:
    pattern = re.compile(r"new\s+PrinterModel\.DataBean\(")
    starts = [m.end() for m in pattern.finditer(text)]
    models = []
    paper_presets = {}

    for start in starts:
        args_str = extract_args(text, start)
        raw_args = split_args(args_str)
        args = [parse_token(tok) for tok in raw_args]
        if len(args) < 25:
            raise ValueError(f"Unexpected arg count: {len(args)} for {raw_args[:3]}")

        model = {
            "model_no": args[0],
            "model": int(args[1]),
            "size": int(args[2]),
            "one_length": int(args[5]),
            "head_name": args[6],
            "dev_dpi": int(args[8]),
            "img_print_speed": int(args[9]),
            "text_print_speed": int(args[10]),
            "img_mtu": int(args[11]),
            "new_compress": bool(args[12]),
            "paper_num": int(args[13]),
            "interval_ms": int(args[14]),
            "thin_energy": int(args[15]),
            "moderation_energy": int(args[16]),
            "deepen_energy": int(args[17]),
            "text_energy": int(args[18]),
            "has_id": bool(args[19]),
            "use_spp": bool(args[20]),
            "new_format": bool(args[21]),
            "can_print_label": bool(args[22]),
            "label_value": str(args[23]),
            "back_paper_num": int(args[24]),
            "a4xii": False,
        }
        if not bool(args[7]):
            model["ble_mtu_request"] = 23

        # A4XII models are the only ones using the signature that ends with two booleans.
        if len(args) == 29 and type(args[-1]) is bool and type(args[-2]) is bool:
            model["a4xii"] = args[-1]
        preset = paper_preset_from_args(args)
        preset_key = paper_preset_key(preset)
        paper_presets[preset_key] = preset
        model["paper_presets"] = [preset_key]

        models.append(model)
    return models, paper_presets


def write_outputs(
    models: list[dict],
    paper_presets: dict[str, dict],
    *,
    models_out: Path,
    paper_presets_out: Path,
) -> None:
    models_out.parent.mkdir(parents=True, exist_ok=True)
    paper_presets_out.parent.mkdir(parents=True, exist_ok=True)
    models_out.write_text(json.dumps(models, indent=2, ensure_ascii=True), encoding="utf-8")
    paper_presets_out.write_text(
        json.dumps(
            {key: paper_presets[key] for key in sorted(paper_presets)},
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract TinyPrint model rows from a decompiled PrintModelUtils.java file.",
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Path to decompiled com/Utils/PrintModelUtils.java",
    )
    parser.add_argument(
        "--models-out",
        type=Path,
        required=True,
        help="Output JSON path for extracted source model rows.",
    )
    parser.add_argument(
        "--paper-presets-out",
        type=Path,
        required=True,
        help="Output JSON path for extracted source paper presets.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    models, paper_presets = generate_models(args.source.read_text(encoding="utf-8"))
    write_outputs(
        models,
        paper_presets,
        models_out=args.models_out,
        paper_presets_out=args.paper_presets_out,
    )
    print(f"Wrote {len(models)} models to {args.models_out}")
    print(f"Wrote {len(paper_presets)} paper presets to {args.paper_presets_out}")


if __name__ == "__main__":
    main()
