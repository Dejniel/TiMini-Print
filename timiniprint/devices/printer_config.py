from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from .device import (
    BluetoothTarget,
    PrinterDevice,
    SerialTarget,
    TransportTarget,
)
from .model_codec import model_from_json, model_to_json
from .profiles import (
    ModeLevelProfile,
    PrinterProfile,
    PrinterRuntimeDefaults,
    RuntimeCapabilities,
    RuntimeSettings,
)

PRINTER_CONFIG_SCHEMA = "timiniprint/printer-config/v1"


@dataclass(frozen=True)
class RuntimeOverrides:
    variant: str | None = None
    defaults_key: str | None = None
    density: ModeLevelProfile | None = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)


@dataclass(frozen=True)
class PrinterConfigParts:
    profile: PrinterProfile
    runtime_settings: RuntimeSettings | None
    display_name: str
    transport_target: TransportTarget | None


def runtime_settings_from_parts(
    *,
    variant: str | None,
    defaults: PrinterRuntimeDefaults | None,
) -> RuntimeSettings | None:
    if variant is None and defaults is None:
        return None
    return RuntimeSettings(
        variant=variant or (None if defaults is None else defaults.variant),
        defaults=defaults,
        capabilities=RuntimeCapabilities() if defaults is None else defaults.capabilities,
    )


def serialize_printer_config(device: PrinterDevice) -> dict[str, Any]:
    return {
        "schema": PRINTER_CONFIG_SCHEMA,
        "profile_key": device.profile.profile_key,
        "profile_overrides": _profile_overrides_for_device(device),
        "runtime_overrides": model_to_json(_runtime_overrides_from_settings(device.runtime_settings)),
        "device": {
            "display_name": device.display_name,
            "transport_target": _serialize_transport_target(device.transport_target),
        },
    }


def parse_printer_config(
    printer_config: Mapping[str, object],
    *,
    require_profile: Callable[[str], PrinterProfile],
    require_runtime_defaults: Callable[[str], PrinterRuntimeDefaults],
) -> PrinterConfigParts:
    schema = str(printer_config.get("schema") or "")
    if schema != PRINTER_CONFIG_SCHEMA:
        raise RuntimeError(f"Unsupported printer config schema '{schema or '<missing>'}'")

    profile = _profile_from_printer_config(printer_config, require_profile=require_profile)
    runtime_settings = _runtime_settings_from_printer_config(
        _require_mapping(printer_config.get("runtime_overrides") or {}, "Printer config runtime_overrides"),
        require_defaults=require_runtime_defaults,
    )
    device_entry = _require_mapping(printer_config.get("device") or {}, "Printer config device")
    return PrinterConfigParts(
        profile=profile,
        runtime_settings=runtime_settings,
        display_name=str(device_entry.get("display_name") or profile.profile_key),
        transport_target=_parse_transport_target(device_entry.get("transport_target")),
    )


def _profile_overrides_for_device(device: PrinterDevice) -> dict[str, Any]:
    entry = model_to_json(device.profile)
    entry.pop("profile_key")
    entry["default_protocol_family"] = device.protocol_family.value
    entry["default_protocol_variant"] = device.protocol_variant
    entry["default_image_pipeline"] = model_to_json(device.image_pipeline)
    return entry


def _profile_from_printer_config(
    printer_config: Mapping[str, object],
    *,
    require_profile: Callable[[str], PrinterProfile],
) -> PrinterProfile:
    profile_key = str(printer_config.get("profile_key") or "")
    if not profile_key:
        raise RuntimeError("Printer config is missing profile_key")
    base_profile = require_profile(profile_key)
    overrides = _require_mapping(
        printer_config.get("profile_overrides") or {},
        "Printer config profile_overrides",
    )
    merged = _deep_merge_json(model_to_json(base_profile), overrides)
    merged["profile_key"] = base_profile.profile_key
    return model_from_json(PrinterProfile, merged)


def _runtime_settings_from_printer_config(
    entry: Mapping[str, object],
    *,
    require_defaults: Callable[[str], PrinterRuntimeDefaults],
) -> RuntimeSettings | None:
    defaults_key = None if entry.get("defaults_key") in (None, "") else str(entry["defaults_key"])
    base_defaults = None if defaults_key is None else require_defaults(defaults_key)
    base_settings = runtime_settings_from_parts(
        variant=None if base_defaults is None else base_defaults.variant,
        defaults=base_defaults,
    )
    base_payload = model_to_json(_runtime_overrides_from_settings(base_settings))
    overrides = model_from_json(
        RuntimeOverrides,
        _deep_merge_json(base_payload, entry),
        path="$.runtime_overrides",
    )

    has_capabilities = overrides.capabilities.d2_status or overrides.capabilities.didian_status
    if overrides.defaults_key is None and overrides.density is None:
        if overrides.variant is None and not has_capabilities:
            return None
        return RuntimeSettings(
            variant=overrides.variant,
            capabilities=overrides.capabilities,
        )

    defaults = None
    if overrides.density is not None:
        defaults = PrinterRuntimeDefaults(
            key=overrides.defaults_key or "printer_config",
            profile_key="printer_config" if base_defaults is None else base_defaults.profile_key,
            variant=overrides.variant,
            density=overrides.density,
            capabilities=overrides.capabilities,
        )
    return RuntimeSettings(
        variant=overrides.variant,
        defaults=defaults,
        capabilities=overrides.capabilities,
    )


def _runtime_overrides_from_settings(runtime_settings: RuntimeSettings | None) -> RuntimeOverrides:
    if runtime_settings is None:
        return RuntimeOverrides()
    return RuntimeOverrides(
        variant=runtime_settings.variant,
        defaults_key=runtime_settings.defaults_key,
        density=None if runtime_settings.defaults is None else runtime_settings.defaults.density,
        capabilities=runtime_settings.capabilities,
    )


def _serialize_transport_target(transport_target: TransportTarget | None) -> dict[str, Any] | None:
    if isinstance(transport_target, SerialTarget):
        payload = model_to_json(transport_target)
        payload["kind"] = "serial"
        return payload
    if isinstance(transport_target, BluetoothTarget):
        payload = model_to_json(transport_target)
        payload["kind"] = "bluetooth"
        return payload
    return None


def _parse_transport_target(value: object) -> TransportTarget | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeError("Invalid transport_target in printer config")
    kind = str(value.get("kind") or "")
    payload = {key: item for key, item in value.items() if key != "kind"}
    if kind == "serial":
        return model_from_json(SerialTarget, payload, path="$.device.transport_target")
    if kind == "bluetooth":
        return model_from_json(BluetoothTarget, payload, path="$.device.transport_target")
    raise RuntimeError(f"Unsupported transport_target kind '{kind or '<missing>'}'")


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


def _deep_merge_json(
    base: Mapping[str, object],
    overrides: Mapping[str, object],
) -> dict[str, object]:
    merged = dict(base)
    for key, value in overrides.items():
        base_value = merged.get(key)
        if isinstance(base_value, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_json(base_value, value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "PRINTER_CONFIG_SCHEMA",
    "PrinterConfigParts",
    "parse_printer_config",
    "runtime_settings_from_parts",
    "serialize_printer_config",
]
