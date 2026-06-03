from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.catalog import RULE_DATA_PATH
from timiniprint.devices.profiles import DetectionNormalizer, DetectionRule
from timiniprint.protocol.families import get_protocol_behavior

README_PATH = REPO_ROOT / "README.md"
INVENTORY_PATH = REPO_ROOT / "timiniprint" / "data" / "readme_model_groups.json"
SUPPORTED_MARKER = "supported-models"
TODO_MARKER = "todo-models"
INVENTORY_SCHEMA = "timiniprint/readme-model-groups/v1"


@dataclass(frozen=True)
class InventoryEntry:
    id: str
    status: str
    primary_names: tuple[str, ...]
    clone_names: tuple[str, ...]
    rule_keys: tuple[str, ...]
    render_mode: str | None
    display_label: str | None
    original_app_name: str | None
    preserve_suffixes: bool
    notes: str | None

    @property
    def visible_names(self) -> tuple[str, ...]:
        return self.primary_names + self.clone_names


def load_inventory_entries(path: Path = INVENTORY_PATH) -> list[InventoryEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("README model inventory must contain a JSON object")
    if raw.get("schema") != INVENTORY_SCHEMA:
        raise ValueError(f"Unsupported README model inventory schema: {raw.get('schema')!r}")
    groups = raw.get("groups")
    if not isinstance(groups, list):
        raise ValueError("README model inventory groups must be a JSON array")
    entries: list[InventoryEntry] = []
    for index, item in enumerate(groups):
        if not isinstance(item, dict):
            raise ValueError(f"README model inventory entry #{index + 1} must be a JSON object")
        primary_names = item.get("primary_names", [])
        clone_names = item.get("clone_names", [])
        rule_keys = item.get("rule_keys", [])
        if not isinstance(primary_names, list) or not all(isinstance(value, str) for value in primary_names):
            raise ValueError(f"Entry {item.get('id', '<missing>')} primary_names must be a JSON string array")
        if not isinstance(clone_names, list) or not all(isinstance(value, str) for value in clone_names):
            raise ValueError(f"Entry {item.get('id', '<missing>')} clone_names must be a JSON string array")
        if not isinstance(rule_keys, list) or not all(isinstance(value, str) for value in rule_keys):
            raise ValueError(f"Entry {item.get('id', '<missing>')} rule_keys must be a JSON string array")
        entries.append(
            InventoryEntry(
                id=str(item.get("id") or ""),
                status=str(item.get("status") or ""),
                primary_names=tuple(value.strip() for value in primary_names if value.strip()),
                clone_names=tuple(value.strip() for value in clone_names if value.strip()),
                rule_keys=tuple(value.strip() for value in rule_keys if value.strip()),
                render_mode=None
                if item.get("render_mode") in (None, "")
                else str(item["render_mode"]).strip(),
                display_label=None
                if item.get("display_label") in (None, "")
                else str(item["display_label"]).strip(),
                original_app_name=None
                if item.get("original_app_name") in (None, "")
                else str(item["original_app_name"]).strip(),
                preserve_suffixes=bool(item.get("preserve_suffixes", False)),
                notes=None if item.get("notes") in (None, "") else str(item["notes"]).strip(),
            )
        )
    return entries


def _rule_names(rule: DetectionRule) -> tuple[set[str], set[str], set[str]]:
    display_names = set(rule.exact_names)
    raw_names = set(rule.exact_names)
    for prefix in rule.prefixes:
        display_names.add(prefix[:-1] if prefix.endswith(("-", "_")) else prefix)
        raw_names.add(prefix)
    return display_names, raw_names, display_names | raw_names


def _normalized_name(value: str) -> str:
    return DetectionNormalizer.normalize_name(value)


def _display_name_sort_key(value: str) -> tuple[str, str]:
    return (value.casefold(), value)


def _public_readme_name(name: str) -> str:
    return name[:-1] if name.endswith(("-", "_")) else name


def _entry_public_name(entry: InventoryEntry, name: str) -> str:
    return name if entry.preserve_suffixes else _public_readme_name(name)


def _entry_alias_merge_key(entry: InventoryEntry, name: str) -> str:
    return _entry_public_name(entry, name).replace("-", "_")


def _dedupe_names(names: tuple[str, ...] | list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _render_primary_name(
    name: str,
    *,
    original_app_name: str | None,
    preserve_suffixes: bool = False,
) -> str:
    public_name = name if preserve_suffixes else _public_readme_name(name)
    if original_app_name:
        return f"{public_name} ({original_app_name})"
    return public_name


def _display_clone_names(entry: InventoryEntry) -> list[str]:
    primary_names = {_entry_alias_merge_key(entry, name) for name in entry.primary_names}
    clone_names = []
    seen = set(primary_names)
    for name in entry.clone_names:
        public_name = _entry_public_name(entry, name)
        merge_key = _entry_alias_merge_key(entry, name)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        clone_names.append(public_name)
    return clone_names


def _primary_group_clone_names(entry: InventoryEntry) -> list[str]:
    if len(entry.primary_names) <= 1:
        return []
    if _renders_inline(entry):
        return []
    clone_names: list[str] = []
    seen = {_entry_alias_merge_key(entry, entry.primary_names[0])}
    for name in entry.primary_names[1:]:
        public_name = _entry_public_name(entry, name)
        merge_key = _entry_alias_merge_key(entry, name)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        clone_names.append(public_name)
    return clone_names


def _entry_clone_names(entry: InventoryEntry) -> list[str]:
    clone_names: list[str] = []
    seen: set[str] = set()
    for name in _primary_group_clone_names(entry) + _display_clone_names(entry):
        public_name = _entry_public_name(entry, name)
        merge_key = _entry_alias_merge_key(entry, name)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        clone_names.append(public_name)
    return clone_names


def _renders_inline(entry: InventoryEntry) -> bool:
    return entry.render_mode == "flat"


def _renders_as_single_name(entry: InventoryEntry) -> bool:
    primary_public_names: list[str] = []
    seen_public_names: set[str] = set()
    for name in entry.primary_names:
        public_name = _entry_public_name(entry, name)
        if public_name in seen_public_names:
            continue
        seen_public_names.add(public_name)
        primary_public_names.append(public_name)
    primary_alias_keys = {_entry_alias_merge_key(entry, name) for name in entry.primary_names}
    if len(primary_alias_keys) != 1:
        return False
    if _entry_clone_names(entry):
        return False
    if entry.display_label is None:
        return True
    return entry.display_label == primary_public_names[0]


def _entry_label(entry: InventoryEntry, *, include_original_app_name: bool) -> str:
    if entry.display_label:
        label = entry.display_label
    else:
        label = _entry_public_name(entry, entry.primary_names[0])
    if include_original_app_name and entry.original_app_name:
        return f"{label} ({entry.original_app_name})"
    return label


def render_supported_models_block(entries: list[InventoryEntry]) -> str:
    supported = [entry for entry in entries if entry.status == "supported"]
    main_names: list[str] = []
    for entry in supported:
        if _renders_inline(entry):
            for name in entry.primary_names:
                main_names.append(
                    _render_primary_name(
                        name,
                        original_app_name=entry.original_app_name,
                        preserve_suffixes=entry.preserve_suffixes,
                    )
                )
            continue
        if _renders_as_single_name(entry):
            main_names.append(
                _render_primary_name(
                    entry.primary_names[0],
                    original_app_name=entry.original_app_name,
                    preserve_suffixes=entry.preserve_suffixes,
                )
            )
            continue
        if entry.display_label or _entry_clone_names(entry):
            continue
        for name in entry.primary_names:
            main_names.append(
                _render_primary_name(
                    name,
                    original_app_name=entry.original_app_name,
                    preserve_suffixes=entry.preserve_suffixes,
                )
            )
    main_names = _dedupe_names(main_names)
    main_names.sort(key=_display_name_sort_key)

    lines = [", ".join(main_names)]
    bullet_entries = [
        entry
        for entry in supported
        if (
            not _renders_inline(entry)
            and not _renders_as_single_name(entry)
            and (entry.display_label or _entry_clone_names(entry))
        )
    ]
    bullet_entries.sort(key=lambda entry: _display_name_sort_key(_entry_label(entry, include_original_app_name=False)))
    for entry in bullet_entries:
        clones = _entry_clone_names(entry)
        label = _entry_label(entry, include_original_app_name=not clones)
        if clones:
            lines.append(f"- {label} and clones: {', '.join(clones)}")
        else:
            lines.append(f"- {label}")
    return "\n".join(lines)


def render_todo_models_block(entries: list[InventoryEntry]) -> str:
    todo = [entry for entry in entries if entry.status == "todo"]
    todo.sort(key=lambda entry: _display_name_sort_key(_entry_label(entry, include_original_app_name=False)))
    main_names: list[str] = []
    lines = []
    for entry in todo:
        if _renders_inline(entry):
            main_names.extend(_public_readme_name(name) for name in entry.primary_names)
            continue
        if _renders_as_single_name(entry):
            main_names.append(_entry_label(entry, include_original_app_name=False))
            continue
        line = f"- {_entry_label(entry, include_original_app_name=False)}"
        clone_names = _entry_clone_names(entry)
        if clone_names:
            line += f" and clones: {', '.join(clone_names)}"
        if entry.notes:
            line += f" — {entry.notes}"
        lines.append(line)
    main_names = _dedupe_names(main_names)
    if main_names:
        return "\n".join([", ".join(main_names)] + lines)
    return "\n".join(lines)


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


def render_readme(entries: list[InventoryEntry], readme_text: str | None = None) -> str:
    text = README_PATH.read_text(encoding="utf-8") if readme_text is None else readme_text
    text = _replace_marked_section(text, SUPPORTED_MARKER, render_supported_models_block(entries))
    text = _replace_marked_section(text, TODO_MARKER, render_todo_models_block(entries))
    return text


def _load_rule_index() -> dict[str, DetectionRule]:
    catalog = PrinterCatalog.load(rule_path=RULE_DATA_PATH)
    return {rule.rule_key: rule for rule in catalog.rules}


def validate_inventory(entries: list[InventoryEntry]) -> list[str]:
    errors: list[str] = []
    rules_by_key = _load_rule_index()
    name_matches: dict[str, set[str]] = {}
    for rule_key, rule in rules_by_key.items():
        _display_names, _raw_names, visible_names = _rule_names(rule)
        for name in visible_names:
            name_matches.setdefault(_normalized_name(name), set()).add(rule_key)

    supported_visible_names: dict[str, str] = {}
    for entry in entries:
        if not entry.id:
            errors.append("README model inventory entry is missing id")
        if entry.status not in {"supported", "todo"}:
            errors.append(f"Entry {entry.id or '<missing>'} has invalid status {entry.status!r}")
        if entry.render_mode not in {None, "flat"}:
            errors.append(f"Entry {entry.id or '<missing>'} has invalid render_mode {entry.render_mode!r}")
        if not entry.primary_names:
            errors.append(f"Entry {entry.id or '<missing>'} must define at least one primary name")
        if len(set(entry.primary_names)) != len(entry.primary_names):
            errors.append(f"Entry {entry.id} has duplicate primary names")
        if len(set(entry.clone_names)) != len(entry.clone_names):
            errors.append(f"Entry {entry.id} has duplicate clone names")
        if set(entry.primary_names) & set(entry.clone_names):
            errors.append(f"Entry {entry.id} repeats the same name as both primary and clone")
        if entry.original_app_name is not None and not entry.original_app_name:
            errors.append(f"Entry {entry.id} has an empty original_app_name")

        allowed_rule_keys = set(entry.rule_keys)
        for rule_key in entry.rule_keys:
            if rule_key not in rules_by_key:
                errors.append(f"Entry {entry.id} references unknown rule_key {rule_key!r}")
                continue
            if entry.status == "supported":
                behavior = get_protocol_behavior(rules_by_key[rule_key].protocol_family)
                if not behavior.implemented:
                    errors.append(
                        f"Entry {entry.id} uses unsupported family {rules_by_key[rule_key].protocol_family.value!r}"
                    )

        for name in entry.visible_names:
            matching_rule_keys = name_matches.get(_normalized_name(name), set())
            if entry.rule_keys:
                matching_rule_keys = matching_rule_keys & allowed_rule_keys
            if entry.status == "supported":
                implemented_matches = [
                    rule_key
                    for rule_key in matching_rule_keys
                    if get_protocol_behavior(rules_by_key[rule_key].protocol_family).implemented
                ]
                if not implemented_matches:
                    errors.append(f"Supported entry {entry.id} name {name!r} does not resolve to an implemented rule")
            elif entry.rule_keys:
                if not matching_rule_keys:
                    errors.append(f"TODO entry {entry.id} name {name!r} does not match its declared rule_keys")
            elif matching_rule_keys:
                errors.append(
                    f"TODO entry {entry.id} name {name!r} already resolves to runtime rules; add explicit rule_keys"
                )

        if entry.status == "supported":
            for name in entry.visible_names:
                public_name = _render_primary_name(
                    name,
                    original_app_name=entry.original_app_name,
                    preserve_suffixes=entry.preserve_suffixes,
                )
                owner = supported_visible_names.get(public_name)
                if owner is not None:
                    if owner != entry.id:
                        errors.append(
                            f"Supported public name {public_name!r} appears in both {owner!r} and {entry.id!r}"
                        )
                else:
                    supported_visible_names[public_name] = entry.id
    return errors


def assert_readme_is_current() -> None:
    entries = load_inventory_entries()
    errors = validate_inventory(entries)
    if errors:
        raise AssertionError("README model inventory validation failed:\n- " + "\n- ".join(errors))
    expected = render_readme(entries)
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

    entries = load_inventory_entries()
    errors = validate_inventory(entries)
    if errors:
        for error in errors:
            print(error)
        return 1
    rendered = render_readme(entries)
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
