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
    RuntimeCapabilities,
    RuntimePreset,
    RuntimeSettings,
)

PRINTER_CONFIG_SCHEMA = "timiniprint/printer-config/v1"


@dataclass(frozen=True)
class RuntimeOverrides:
    control_algorithm: str | None = None
    preset_key: str | None = None
    density: ModeLevelProfile | None = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)


@dataclass(frozen=True)
class PrinterConfigParts:
    profile: PrinterProfile
    runtime_settings: RuntimeSettings | None
    display_name: str
    transport_target: TransportTarget | None
    model_key: str | None = None
    origin_app_packages: tuple[str, ...] = ()


def runtime_settings_from_parts(
    *,
    preset: RuntimePreset | None,
) -> RuntimeSettings | None:
    if preset is None:
        return None
    return RuntimeSettings(
        control_algorithm=preset.control_algorithm,
        preset=preset,
        capabilities=preset.capabilities,
    )


def serialize_printer_config(
    device: PrinterDevice,
    *,
    model_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": PRINTER_CONFIG_SCHEMA,
        "profile_key": device.profile.profile_key,
        "profile_overrides": _profile_overrides_for_device(device),
        "runtime_overrides": model_to_json(_runtime_overrides_from_settings(device.runtime_settings)),
        "device": {
            "display_name": device.display_name,
            "transport_target": _serialize_transport_target(device.transport_target),
        },
    }
    if model_key is not None:
        payload["model_key"] = model_key
    return payload


def parse_printer_config(
    printer_config: Mapping[str, object],
    *,
    require_profile: Callable[[str], PrinterProfile],
    require_model_device: Callable[[str], PrinterDevice],
    require_runtime_preset: Callable[[PrinterProfile, str], RuntimePreset],
) -> PrinterConfigParts:
    schema = str(printer_config.get("schema") or "")
    if schema != PRINTER_CONFIG_SCHEMA:
        raise RuntimeError(f"Unsupported printer config schema '{schema or '<missing>'}'")

    base_device = _base_device_from_printer_config(
        printer_config,
        require_profile=require_profile,
        require_model_device=require_model_device,
    )
    profile = _profile_from_printer_config(printer_config, base_device=base_device)
    runtime_settings = _runtime_settings_from_printer_config(
        _require_mapping(printer_config.get("runtime_overrides") or {}, "Printer config runtime_overrides"),
        base_settings=base_device.runtime_settings,
        profile=profile,
        require_preset=require_runtime_preset,
    )
    device_entry = _require_mapping(printer_config.get("device") or {}, "Printer config device")
    return PrinterConfigParts(
        profile=profile,
        runtime_settings=runtime_settings,
        display_name=str(device_entry.get("display_name") or profile.profile_key),
        transport_target=_parse_transport_target(device_entry.get("transport_target")),
        model_key=None if printer_config.get("model_key") in (None, "") else base_device.model_key,
        origin_app_packages=base_device.origin_app_packages,
    )


def _profile_overrides_for_device(device: PrinterDevice) -> dict[str, Any]:
    entry = _profile_payload_for_device(device)
    entry.pop("profile_key", None)
    entry.pop("runtime_presets", None)
    return entry


def _profile_payload_for_device(device: PrinterDevice) -> dict[str, Any]:
    entry = model_to_json(device.profile)
    entry["protocol_default"] = {
        "type": device.protocol_family.value,
        "packets_type": device.protocol_variant,
    }
    entry["default_image_pipeline"] = model_to_json(device.image_pipeline)
    return entry


def _base_device_from_printer_config(
    printer_config: Mapping[str, object],
    *,
    require_profile: Callable[[str], PrinterProfile],
    require_model_device: Callable[[str], PrinterDevice],
) -> PrinterDevice:
    model_key = None if printer_config.get("model_key") in (None, "") else str(printer_config["model_key"])
    if model_key is not None:
        base_device = require_model_device(model_key)
        profile_key = None if printer_config.get("profile_key") in (None, "") else str(printer_config["profile_key"])
        if profile_key is not None and profile_key != base_device.profile_key:
            raise RuntimeError(
                f"Printer config profile_key {profile_key!r} does not match model "
                f"{model_key!r} profile {base_device.profile_key!r}"
            )
        return base_device

    profile_key = str(printer_config.get("profile_key") or "")
    if not profile_key:
        raise RuntimeError("Printer config is missing model_key or profile_key")
    base_profile = require_profile(profile_key)
    return PrinterDevice(
        display_name=profile_key,
        profile=base_profile,
        protocol_family=base_profile.protocol_default.type,
        protocol_variant=base_profile.protocol_default.packets_type,
        image_pipeline=base_profile.default_image_pipeline,
    )


def _profile_from_printer_config(
    printer_config: Mapping[str, object],
    *,
    base_device: PrinterDevice,
) -> PrinterProfile:
    overrides = _require_mapping(
        printer_config.get("profile_overrides") or {},
        "Printer config profile_overrides",
    )
    merged = _deep_merge_json(_profile_payload_for_device(base_device), overrides)
    merged["profile_key"] = base_device.profile_key
    return model_from_json(PrinterProfile, merged)


def _runtime_settings_from_printer_config(
    entry: Mapping[str, object],
    *,
    base_settings: RuntimeSettings | None,
    profile: PrinterProfile,
    require_preset: Callable[[PrinterProfile, str], RuntimePreset],
) -> RuntimeSettings | None:
    if "preset_key" in entry:
        preset_key = None if entry.get("preset_key") in (None, "") else str(entry["preset_key"])
    else:
        preset_key = None if base_settings is None else base_settings.preset_key
    base_preset = None if preset_key is None else require_preset(profile, preset_key)
    base_settings = runtime_settings_from_parts(preset=base_preset)
    base_payload = model_to_json(_runtime_overrides_from_settings(base_settings))
    overrides = model_from_json(
        RuntimeOverrides,
        _deep_merge_json(base_payload, entry),
        path="$.runtime_overrides",
    )

    has_capabilities = overrides.capabilities.d2_status or overrides.capabilities.didian_status
    if overrides.preset_key is None and overrides.density is None:
        if overrides.control_algorithm is None and not has_capabilities:
            return None
        return RuntimeSettings(
            control_algorithm=overrides.control_algorithm,
            capabilities=overrides.capabilities,
        )

    preset = None
    if overrides.density is not None:
        preset = RuntimePreset(
            key=overrides.preset_key or "printer_config",
            control_algorithm=overrides.control_algorithm,
            density=overrides.density,
            capabilities=overrides.capabilities,
        )
    elif overrides.preset_key is not None:
        preset = require_preset(profile, overrides.preset_key)
    return RuntimeSettings(
        control_algorithm=overrides.control_algorithm,
        preset=preset,
        capabilities=overrides.capabilities,
    )


def _runtime_overrides_from_settings(runtime_settings: RuntimeSettings | None) -> RuntimeOverrides:
    if runtime_settings is None:
        return RuntimeOverrides()
    return RuntimeOverrides(
        control_algorithm=runtime_settings.control_algorithm,
        preset_key=runtime_settings.preset_key,
        density=None if runtime_settings.preset is None else runtime_settings.preset.density,
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
