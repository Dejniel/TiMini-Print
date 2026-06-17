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
from timiniprint.devices.profiles import PrinterProfile, SupportedPrinterModel, UnsupportedPrinterModel  # noqa: E402


def _sample_names(model: dict[str, Any]) -> list[str]:
    samples: list[str] = []
    for named_detection in model.get("detections", []):
        detection = named_detection.get("detection", {})
        for name in detection.get("exact_names", []):
            samples.append(str(name))
        for prefix in detection.get("prefixes", []):
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


def _sample_addresses(model: dict[str, Any]) -> list[str | None]:
    suffixes = [
        str(value).upper()
        for named_detection in model.get("detections", [])
        for value in named_detection.get("detection", {}).get("mac_suffixes", [])
    ]
    if suffixes:
        return [f"AA:BB:CC:DD:EE:{suffix}" for suffix in suffixes]
    return [None, "AA:BB:CC:DD:EE:00"]


def _find_model_reachability_error(catalog: PrinterCatalog, model: dict[str, Any]) -> dict[str, Any] | None:
    samples = _sample_names(model)
    addresses = _sample_addresses(model)
    blocking: dict[str, Any] | None = None
    for sample in samples:
        for address in addresses:
            resolved = catalog.detect_device(sample, address=address)
            if resolved is None:
                continue
            if resolved.model_key == model["model_key"]:
                return None
            if blocking is None:
                blocking = {
                    "kind": "shadowed_model",
                    "model_key": model["model_key"],
                    "sample_name": sample,
                    "sample_address": address,
                    "blocked_by_model_key": resolved.model_key,
                    "expected_profile_key": model["profile_key"],
                    "expected_protocol_family": (model.get("protocol_override") or {}).get("type"),
                    "actual_profile_key": resolved.profile_key,
                    "actual_protocol_family": resolved.protocol_family.value,
                }
    return blocking or {
        "kind": "unreachable_model",
        "model_key": model["model_key"],
        "sample_names": samples[:5],
        "sample_addresses": addresses,
    }


def _find_unsupported_model_reachability_error(
    catalog: PrinterCatalog,
    model: dict[str, Any],
) -> dict[str, Any] | None:
    samples = _sample_names(model)
    addresses = _sample_addresses(model)
    blocking: dict[str, Any] | None = None
    for sample in samples:
        for address in addresses:
            supported = catalog.detect_device(sample, address=address)
            if supported is not None:
                return {
                    "kind": "unsupported_model_matches_supported_model",
                    "model_key": model["model_key"],
                    "sample_name": sample,
                    "sample_address": address,
                    "supported_model_key": supported.model_key,
                    "supported_profile_key": supported.profile_key,
                }
            resolved = catalog.detect_unsupported_model(sample, address=address)
            if resolved is None:
                continue
            if resolved.model_key == model["model_key"]:
                return None
            if blocking is None:
                blocking = {
                    "kind": "shadowed_unsupported_model",
                    "model_key": model["model_key"],
                    "sample_name": sample,
                    "sample_address": address,
                    "blocked_by_model_key": resolved.model_key,
                }
    return blocking or {
        "kind": "unreachable_unsupported_model",
        "model_key": model["model_key"],
        "sample_names": samples[:5],
        "sample_addresses": addresses,
    }


def _model_merge_key(model: dict[str, Any]) -> str:
    """Technical model body; names and detection triggers live under detections."""
    return json.dumps(
        {
            key: value
            for key, value in model.items()
            if key not in {"model_key", "detections", "origin_app_packages"}
        },
        sort_keys=True,
    )


def generate_report(
    profile_path: Path | None = None,
    model_path: Path | None = None,
    unsupported_model_path: Path | None = None,
) -> dict[str, Any]:
    default_profile_path = REPO_ROOT / "timiniprint/data/printer_profiles.json"
    default_model_path = REPO_ROOT / "timiniprint/data/printer_models.json"
    default_unsupported_model_path = REPO_ROOT / "timiniprint/data/printer_models_unsupported.json"
    profile_path = profile_path or default_profile_path
    model_path = model_path or default_model_path
    if unsupported_model_path is None and profile_path == default_profile_path and model_path == default_model_path:
        unsupported_model_path = default_unsupported_model_path
    profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
    models_raw = json.loads(model_path.read_text(encoding="utf-8"))
    unsupported_models_raw = (
        []
        if unsupported_model_path is None
        else json.loads(unsupported_model_path.read_text(encoding="utf-8"))
    )
    if (
        profile_path == default_profile_path
        and model_path == default_model_path
        and unsupported_model_path == default_unsupported_model_path
    ):
        catalog = PrinterCatalog.load(
            profile_path=profile_path,
            model_path=model_path,
            unsupported_model_path=unsupported_model_path,
        )
    else:
        catalog = PrinterCatalog(
            [model_from_json(PrinterProfile, entry) for entry in profiles_raw],
            [model_from_json(SupportedPrinterModel, entry) for entry in models_raw],
            [model_from_json(UnsupportedPrinterModel, entry) for entry in unsupported_models_raw],
        )

    referenced_profiles = {model["profile_key"] for model in models_raw}
    all_profiles = {profile["profile_key"] for profile in profiles_raw}

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for model in models_raw:
        for named_detection in model.get("detections", []):
            detection = named_detection.get("detection", {})
            for field in ("prefixes", "exact_names"):
                for trigger in detection.get(field, []):
                    if trigger != trigger.strip():
                        errors.append(
                            {
                                "kind": "trigger_whitespace",
                                "model_key": model["model_key"],
                                "name": named_detection.get("name"),
                                "field": field,
                                "trigger": trigger,
                            }
                        )

    for model in unsupported_models_raw:
        if not model.get("origin_app_packages"):
            errors.append(
                {
                    "kind": "missing_unsupported_model_origin_app_packages",
                    "model_key": model["model_key"],
                }
            )
        if model.get("model_group") in {
            detection.get("name")
            for supported_model in models_raw
            for detection in supported_model.get("detections", [])
        }:
            errors.append(
                {
                    "kind": "unsupported_model_group_matches_supported_name",
                    "model_key": model["model_key"],
                    "model_group": model.get("model_group"),
                }
            )
        for named_detection in model.get("detections", []):
            detection = named_detection.get("detection", {})
            if detection.get("exact_names"):
                errors.append(
                    {
                        "kind": "unsupported_exact_names_not_allowed",
                        "model_key": model["model_key"],
                        "name": named_detection.get("name"),
                        "exact_names": detection.get("exact_names"),
                    }
                )
            for field in ("prefixes", "exact_names"):
                for trigger in detection.get(field, []):
                    if trigger != trigger.strip():
                        errors.append(
                            {
                                "kind": "unsupported_trigger_whitespace",
                                "model_key": model["model_key"],
                                "name": named_detection.get("name"),
                                "field": field,
                                "trigger": trigger,
                            }
                        )

    for model in models_raw:
        if model["profile_key"] not in all_profiles:
            errors.append(
                {
                    "kind": "unknown_profile_reference",
                    "model_key": model["model_key"],
                    "profile_key": model["profile_key"],
                }
            )

    for model in models_raw:
        if not model.get("origin_app_packages"):
            errors.append(
                {
                    "kind": "missing_model_origin_app_packages",
                    "model_key": model["model_key"],
                }
            )

    for model in models_raw:
        reachability_error = _find_model_reachability_error(catalog, model)
        if reachability_error is not None:
            errors.append(reachability_error)

    for model in unsupported_models_raw:
        reachability_error = _find_unsupported_model_reachability_error(catalog, model)
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

    duplicate_models: dict[str, list[str]] = defaultdict(list)
    mergeable_models: dict[str, list[str]] = defaultdict(list)
    for model in models_raw:
        canonical_body = json.dumps({k: v for k, v in model.items() if k != "model_key"}, sort_keys=True)
        duplicate_models[canonical_body].append(model["model_key"])
        mergeable_models[_model_merge_key(model)].append(model["model_key"])
    for keys in sorted(duplicate_models.values()):
        if len(keys) > 1:
            errors.append(
                {
                    "kind": "duplicate_model_body",
                    "model_keys": keys,
                }
            )
    for keys in sorted(mergeable_models.values()):
        if len(keys) > 1:
            errors.append(
                {
                    "kind": "mergeable_model_group",
                    "model_keys": keys,
                }
            )

    duplicate_unsupported_models: dict[str, list[str]] = defaultdict(list)
    for model in unsupported_models_raw:
        canonical_body = json.dumps({k: v for k, v in model.items() if k != "model_key"}, sort_keys=True)
        duplicate_unsupported_models[canonical_body].append(model["model_key"])
    for keys in sorted(duplicate_unsupported_models.values()):
        if len(keys) > 1:
            errors.append(
                {
                    "kind": "duplicate_unsupported_model_body",
                    "model_keys": keys,
                }
            )

    return {
        "summary": {
            "profiles": len(catalog.profiles),
            "models": len(catalog.models),
            "unsupported_models": len(catalog.unsupported_models),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit printer profiles and models for dead or malformed data.")
    parser.add_argument("--out", help="Write the full audit report as JSON to this path.")
    args = parser.parse_args()

    report = generate_report()
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "Catalog audit: "
        f"{report['summary']['profiles']} profiles, "
        f"{report['summary']['models']} models, "
        f"{report['summary']['unsupported_models']} unsupported models, "
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
