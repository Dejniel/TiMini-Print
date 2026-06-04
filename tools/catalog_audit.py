from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO_ROOT))

from timiniprint.devices import PrinterCatalog  # noqa: E402
from timiniprint.devices.model_codec import model_from_json  # noqa: E402
from timiniprint.devices.profiles import DetectionRule, PrinterProfile  # noqa: E402


def _sample_names(rule: dict[str, Any]) -> list[str]:
    samples: list[str] = []
    for name in rule.get("exact_names", []):
        samples.append(str(name))
    for prefix in rule.get("prefixes", []):
        prefix = str(prefix)
        if prefix.endswith("-"):
            samples.append(prefix + "ABCD")
        else:
            samples.append(prefix)
            if "-" not in prefix:
                samples.append(prefix + "-ABCD")
    deduped: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        if sample in seen:
            continue
        seen.add(sample)
        deduped.append(sample)
    return deduped


def _sample_addresses(rule: dict[str, Any]) -> list[str | None]:
    suffixes = [str(value).upper() for value in rule.get("mac_suffixes", [])]
    if suffixes:
        return [f"AA:BB:CC:DD:EE:{suffix}" for suffix in suffixes]
    return [None, "AA:BB:CC:DD:EE:00"]


def _find_rule_reachability_error(catalog: PrinterCatalog, rule: dict[str, Any]) -> dict[str, Any] | None:
    samples = _sample_names(rule)
    addresses = _sample_addresses(rule)
    blocking: dict[str, Any] | None = None
    for sample in samples:
        for address in addresses:
            resolved = catalog.detect_device(sample, address=address)
            if resolved is None:
                continue
            if resolved.detection_rule_key == rule["rule_key"]:
                return None
            if blocking is None:
                blocking = {
                    "kind": "shadowed_rule",
                    "rule_key": rule["rule_key"],
                    "sample_name": sample,
                    "sample_address": address,
                    "blocked_by_rule_key": resolved.detection_rule_key,
                    "expected_profile_key": rule["profile_key"],
                    "expected_protocol_family": rule["protocol_family"],
                    "actual_profile_key": resolved.profile_key,
                    "actual_protocol_family": resolved.protocol_family.value,
                }
    return blocking or {
        "kind": "unreachable_rule",
        "rule_key": rule["rule_key"],
        "sample_names": samples[:5],
        "sample_addresses": addresses,
    }


def generate_report(
    profile_path: Path | None = None,
    rule_path: Path | None = None,
    runtime_defaults_path: Path | None = None,
) -> dict[str, Any]:
    default_profile_path = REPO_ROOT / "timiniprint/data/printer_profiles.json"
    default_rule_path = REPO_ROOT / "timiniprint/data/printer_detection_rules.json"
    default_runtime_defaults_path = REPO_ROOT / "timiniprint/data/printer_runtime_defaults.json"
    profile_path = profile_path or default_profile_path
    rule_path = rule_path or default_rule_path
    profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
    rules_raw = json.loads(rule_path.read_text(encoding="utf-8"))
    if runtime_defaults_path is not None or (
        profile_path == default_profile_path and rule_path == default_rule_path
    ):
        catalog = PrinterCatalog.load(
            profile_path=profile_path,
            rule_path=rule_path,
            runtime_defaults_path=runtime_defaults_path or default_runtime_defaults_path,
        )
    else:
        catalog = PrinterCatalog(
            [model_from_json(PrinterProfile, entry) for entry in profiles_raw],
            [model_from_json(DetectionRule, entry) for entry in rules_raw],
        )

    referenced_profiles = {rule["profile_key"] for rule in rules_raw}
    all_profiles = {profile["profile_key"] for profile in profiles_raw}

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for rule in rules_raw:
        for field in ("prefixes", "exact_names"):
            for trigger in rule.get(field, []):
                if trigger != trigger.strip():
                    errors.append(
                        {
                            "kind": "trigger_whitespace",
                            "rule_key": rule["rule_key"],
                            "field": field,
                            "trigger": trigger,
                        }
                    )

    for rule in rules_raw:
        if rule["profile_key"] not in all_profiles:
            errors.append(
                {
                    "kind": "unknown_profile_reference",
                    "rule_key": rule["rule_key"],
                    "profile_key": rule["profile_key"],
                }
            )

    for profile in profiles_raw:
        if not profile.get("origin_app_packages"):
            errors.append(
                {
                    "kind": "missing_profile_origin_app_packages",
                    "profile_key": profile["profile_key"],
                }
            )

    for rule in rules_raw:
        if not rule.get("origin_app_packages"):
            errors.append(
                {
                    "kind": "missing_rule_origin_app_packages",
                    "rule_key": rule["rule_key"],
                }
            )

    for rule in rules_raw:
        reachability_error = _find_rule_reachability_error(catalog, rule)
        if reachability_error is not None:
            errors.append(reachability_error)

    for profile_key in sorted(all_profiles - referenced_profiles):
        errors.append(
            {
                "kind": "unreferenced_profile",
                "profile_key": profile_key,
            }
        )

    duplicate_profiles: dict[str, list[str]] = defaultdict(list)
    for profile in profiles_raw:
        profile_key = profile["profile_key"]
        canonical_body = json.dumps({k: v for k, v in profile.items() if k != "profile_key"}, sort_keys=True)
        duplicate_profiles[canonical_body].append(profile_key)
    for keys in sorted(duplicate_profiles.values()):
        if len(keys) > 1:
            errors.append(
                {
                    "kind": "duplicate_profile_body",
                    "profile_keys": keys,
                }
            )

    duplicate_rules: dict[str, list[str]] = defaultdict(list)
    for rule in rules_raw:
        canonical_body = json.dumps({k: v for k, v in rule.items() if k != "rule_key"}, sort_keys=True)
        duplicate_rules[canonical_body].append(rule["rule_key"])
    for keys in sorted(duplicate_rules.values()):
        if len(keys) > 1:
            errors.append(
                {
                    "kind": "duplicate_rule_body",
                    "rule_keys": keys,
                }
            )

    return {
        "summary": {
            "profiles": len(catalog.profiles),
            "rules": len(catalog.rules),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit printer profiles and detection rules for dead or malformed data.")
    parser.add_argument("--out", help="Write the full audit report as JSON to this path.")
    args = parser.parse_args()

    report = generate_report()
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "Catalog audit: "
        f"{report['summary']['profiles']} profiles, "
        f"{report['summary']['rules']} rules, "
        f"{report['summary']['error_count']} errors, "
        f"{report['summary']['warning_count']} warnings"
    )
    for entry in report["errors"]:
        print(f"ERROR {entry['kind']}: {json.dumps(entry, sort_keys=True)}")
    for entry in report["warnings"]:
        print(f"WARNING {entry['kind']}: {json.dumps(entry, sort_keys=True)}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
