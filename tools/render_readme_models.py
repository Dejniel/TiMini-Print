from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import SupportedPrinterModel, UnsupportedPrinterModel

README_PATH = REPO_ROOT / "README.md"
SUPPORTED_MARKER = "supported-models"
TODO_MARKER = "todo-models"
ReadablePrinterModel = SupportedPrinterModel | UnsupportedPrinterModel


def _display_name_sort_key(value: str) -> tuple[str, str]:
    return (value.casefold(), value)


def _public_readme_name(name: str) -> str:
    return name[:-1] if name.endswith(("-", "_")) else name


def _dedupe_names(names: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in names:
        public_name = _public_readme_name(name.strip())
        if not public_name or public_name in seen:
            continue
        seen.add(public_name)
        ordered.append(public_name)
    return ordered


def _profile_key_prediction_label(value: str) -> str:
    tokens = value.split("_")
    if tokens[-1:] == ["legacy"]:
        return "-".join(token.upper() for token in tokens[:-1]) + " legacy"
    if len(tokens) > 1 and tokens[0] in {"pm"}:
        return "-".join(token.upper() for token in tokens)
    return "_".join(token.upper() for token in tokens)


def _render_model_names(models: list[ReadablePrinterModel]) -> str:
    groups_by_key: dict[str, list[str]] = {}
    for model in models:
        names = _dedupe_names(list(model.names))
        if not names:
            continue
        prediction = getattr(model, "profile_key_prediction", None)
        if prediction:
            group_key = prediction
            group = groups_by_key.setdefault(group_key, [_profile_key_prediction_label(prediction)])
            group.extend(names)
            continue
        groups_by_key.setdefault(names[0], []).extend(names)
    singles: list[str] = []
    clone_entries: list[str] = []
    for names in groups_by_key.values():
        names = _dedupe_names(names)
        primary = names[0]
        if len(names) <= 1:
            singles.append(primary)
            continue
        clone_entries.append(f"{primary} and clones: {', '.join(names[1:])}")
    singles.sort(key=_display_name_sort_key)
    clone_entries = [
        entry
        for entry in clone_entries
    ]
    clone_entries.sort(key=_display_name_sort_key)
    chunks: list[str] = []
    if singles:
        chunks.append(", ".join(singles))
    if clone_entries:
        chunks.append("\n".join(f"- {entry}" for entry in clone_entries))
    return "\n\n".join(chunks)


def render_supported_models_block(models: list[SupportedPrinterModel] | None = None) -> str:
    catalog = PrinterCatalog.load()
    return _render_model_names(catalog.models if models is None else models)


def render_todo_models_block(models: list[UnsupportedPrinterModel] | None = None) -> str:
    catalog = PrinterCatalog.load()
    return _render_model_names(catalog.unsupported_models if models is None else models)


def _replace_marked_section(text: str, marker: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(<!-- BEGIN {re.escape(marker)} -->\n)(.*?)(<!-- END {re.escape(marker)} -->)",
        re.S,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"README is missing marker section {marker!r}")
    normalized_replacement = replacement.rstrip("\n") + "\n"
    return text[: match.start(2)] + normalized_replacement + text[match.end(2) :]


def render_readme(readme_text: str | None = None) -> str:
    text = README_PATH.read_text(encoding="utf-8") if readme_text is None else readme_text
    text = _replace_marked_section(text, SUPPORTED_MARKER, render_supported_models_block())
    text = _replace_marked_section(text, TODO_MARKER, render_todo_models_block())
    return text


def validate_catalog_models() -> list[str]:
    catalog = PrinterCatalog.load()
    errors: list[str] = []
    supported_origins_by_name: dict[str, set[str]] = {}
    for model in catalog.models:
        for name in model.names:
            supported_origins_by_name.setdefault(_public_readme_name(name), set()).update(
                model.origin_app_packages
            )
    for model in catalog.models:
        if not model.names:
            errors.append(f"Supported model {model.model_key} has no names")
    for model in catalog.unsupported_models:
        if not model.names:
            errors.append(f"Unsupported model {model.model_key} has no names")
        for name in model.names:
            public_name = _public_readme_name(name)
            supported_origins = supported_origins_by_name.get(public_name)
            if supported_origins and set(model.origin_app_packages).issubset(supported_origins):
                errors.append(
                    f"Unsupported model {model.model_key} display name {public_name!r} is already supported"
                )
    return errors


def assert_readme_is_current() -> None:
    errors = validate_catalog_models()
    if errors:
        raise AssertionError("README model validation failed:\n- " + "\n- ".join(errors))
    expected = render_readme()
    current = README_PATH.read_text(encoding="utf-8")
    if expected != current:
        raise AssertionError("README model sections are out of date; run tools/render_readme_models.py --write")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render and validate README printer model sections.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the generated README sections in place",
    )
    args = parser.parse_args(argv)

    errors = validate_catalog_models()
    if errors:
        for error in errors:
            print(error)
        return 1
    rendered = render_readme()
    if args.write:
        README_PATH.write_text(rendered, encoding="utf-8")
        return 0
    current = README_PATH.read_text(encoding="utf-8")
    if rendered != current:
        print("README model sections are out of date; run tools/render_readme_models.py --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
