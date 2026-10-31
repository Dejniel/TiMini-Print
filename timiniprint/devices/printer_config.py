from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

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

PRINTER_CONFIG_SCHEMA = "timiniprint/printer-config/v3"
PRINTER_CONFIG_SCHEMA_V1 = "timiniprint/printer-config/v1"
PRINTER_CONFIG_SCHEMA_V2 = "timiniprint/printer-config/v2"


@dataclass(frozen=True)
class RuntimeOverrides:
    control_algorithm: Optional[str] = None
    preset_key: Optional[str] = None
    density: Optional[ModeLevelProfile] = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)


@dataclass(frozen=True)
class PrinterConfigParts:
    profile: PrinterProfile
    runtime_settings: Optional[RuntimeSettings]
    display_name: str
    transport_target: Optional[TransportTarget]
    model_key: Optional[str] = None
    origin_ids: Tuple[str, ...] = ()
    preparation_recipe: Optional[str] = None


def runtime_settings_from_parts(
    *,
    preset: Optional[RuntimePreset],
) -> Optional[RuntimeSettings]:
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
    model_key: Optional[str] = None,
    standalone: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema": PRINTER_CONFIG_SCHEMA,
        "profile_key": device.profile.profile_key,
        "profile_overrides": _profile_overrides_for_device(device),
        "runtime_overrides": model_to_json(_runtime_overrides_from_settings(device.runtime_settings)),
        "device": {
            "display_name": device.display_name,
            "origin_ids": list(device.origin_ids),
            "transport_target": _serialize_transport_target(device.transport_target),
        },
    }
    if model_key is not None:
        payload["model_key"] = model_key
    if standalone:
        if model_key is not None:
            raise ValueError("A standalone printer config cannot select a catalog model")
        payload["profile"] = _profile_payload_for_device(device)
        payload["profile_overrides"] = {}
    if device.preparation_recipe is not None:
        payload["device"]["preparation_recipe"] = device.preparation_recipe
    return payload


def parse_printer_config(
    printer_config: Mapping[str, object],
    *,
    require_profile: Callable[[str], PrinterProfile],
    require_model_device: Callable[[str], PrinterDevice],
    require_runtime_preset: Callable[[PrinterProfile, str], RuntimePreset],
) -> PrinterConfigParts:
    schema = str(printer_config.get("schema") or "")
    if schema not in (PRINTER_CONFIG_SCHEMA, PRINTER_CONFIG_SCHEMA_V1, PRINTER_CONFIG_SCHEMA_V2):
        raise RuntimeError(f"Unsupported printer config schema '{schema or '<missing>'}'")
    device_entry = _require_mapping(printer_config.get("device") or {}, "Printer config device")
    if schema == PRINTER_CONFIG_SCHEMA_V1 and ("profile" in printer_config or "preparation_recipe" in device_entry):
        raise ValueError("Standalone profiles and preparation_recipe require printer-config/v2 or later")

    base_device = None
    if schema != PRINTER_CONFIG_SCHEMA:
        if "profile" in printer_config:
            raw_profile = _require_mapping(printer_config["profile"], "Printer config profile")
            protocol = _require_mapping(raw_profile.get("protocol_default") or {}, "Protocol default")
            fallback_family, fallback_variant = protocol.get("type"), protocol.get("packets_type")
        else:
            base_device = _base_device_from_printer_config(
                printer_config, require_profile=require_profile, require_model_device=require_model_device,
            )
            fallback_family = base_device.protocol_family
            fallback_variant = base_device.protocol_variant
        printer_config = _migrate_legacy_layout(printer_config, fallback_family, fallback_variant)
    base_device = base_device or _base_device_from_printer_config(
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
    origin_ids = device_entry.get("origin_ids", base_device.origin_ids)
    if not isinstance(origin_ids, (list, tuple)) or any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in origin_ids
    ):
        raise RuntimeError("Printer config device.origin_ids must be a list of nonblank source IDs")
    if printer_config.get("model_key") and not set(origin_ids).issubset(base_device.origin_ids):
        raise RuntimeError("Printer config device.origin_ids must belong to the selected model")
    preparation_recipe = model_from_json(
        Optional[str], device_entry.get("preparation_recipe"), path="$.device.preparation_recipe",
    )
    if preparation_recipe is not None and (not preparation_recipe or preparation_recipe != preparation_recipe.strip()):
        raise ValueError("$.device.preparation_recipe must be a nonblank recipe ID")
    return PrinterConfigParts(
        profile=profile,
        runtime_settings=runtime_settings,
        display_name=str(device_entry.get("display_name") or profile.profile_key),
        transport_target=_parse_transport_target(device_entry.get("transport_target")),
        model_key=None if printer_config.get("model_key") in (None, "") else base_device.model_key,
        origin_ids=tuple(dict.fromkeys(origin_ids)),
        preparation_recipe=preparation_recipe,
    )


def _migrate_legacy_layout(
    config: Mapping[str, object], fallback_family: object, fallback_variant: str | None,
) -> dict[str, object]:
    """Translate v1/v2 placement once at import, never in the rendering path."""
    from ..protocol.families import get_protocol_behavior

    migrated = dict(config)
    overrides = _require_mapping(config.get("profile_overrides") or {}, "Printer config profile_overrides")
    protocol = _require_mapping(overrides.get("protocol_default") or {}, "Protocol default")
    family = protocol.get("type", fallback_family)
    handles_origin = get_protocol_behavior(family).handles_raster_x_offset_for(
        protocol.get("packets_type", fallback_variant),
    )
    for name in ("profile", "profile_overrides"):
        section = config.get(name)
        if not isinstance(section, Mapping) or "paper_presets" not in section:
            continue
        papers = section["paper_presets"]
        if not isinstance(papers, (list, tuple)):
            raise ValueError("Printer config paper_presets must be an array")
        converted = []
        for entry in papers:
            paper = dict(_require_mapping(entry, "Paper preset"))
            alignment = paper.pop("horizontal_alignment", None)
            if alignment not in (None, "left", "center"):
                raise ValueError("Invalid legacy horizontal alignment")
            origin = paper.get("left_padding_px", 0)
            if origin is not None and (type(origin) is not int or origin < 0):
                raise ValueError("Invalid legacy left_padding_px")
            row = paper.get("raster_width_px")
            if row is not None and (type(row) is not int or row <= 0):
                raise ValueError("Invalid legacy raster_width_px")
            if origin:
                if handles_origin:
                    if row is not None:
                        paper["raster_width_px"] = row + origin
                else:
                    # Older layouts used a nonzero offset to suppress generic
                    # centering even in families that never consumed the offset.
                    paper["raster_width_px"] = row or paper.get("render_width_px")
                    origin = 0
            elif alignment == "left":
                if paper.get("raster_width_px") is None:
                    paper["raster_width_px"] = paper.get("paper_width_px")
            else:
                origin = None
            paper["left_padding_px"] = origin
            converted.append(paper)
        migrated[name] = {**section, "paper_presets": converted}
    migrated["schema"] = PRINTER_CONFIG_SCHEMA
    return migrated


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
    if "profile" in printer_config:
        if printer_config.get("model_key"):
            raise ValueError("A standalone printer config cannot select a catalog model")
        profile = model_from_json(PrinterProfile, printer_config["profile"], path="$.profile")
        key = printer_config.get("profile_key", profile.profile_key)
        if key != profile.profile_key:
            raise ValueError("Printer config profile_key does not match the inline profile")
        return PrinterDevice(profile.profile_key, profile, profile.protocol_default.type,
                             profile.protocol_default.packets_type, profile.default_image_pipeline)
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
    base_settings: Optional[RuntimeSettings],
    profile: PrinterProfile,
    require_preset: Callable[[PrinterProfile, str], RuntimePreset],
) -> Optional[RuntimeSettings]:
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


def _runtime_overrides_from_settings(runtime_settings: Optional[RuntimeSettings]) -> RuntimeOverrides:
    if runtime_settings is None:
        return RuntimeOverrides()
    return RuntimeOverrides(
        control_algorithm=runtime_settings.control_algorithm,
        preset_key=runtime_settings.preset_key,
        density=None if runtime_settings.preset is None else runtime_settings.preset.density,
        capabilities=runtime_settings.capabilities,
    )


def _serialize_transport_target(transport_target: Optional[TransportTarget]) -> Optional[dict[str, Any]]:
    if isinstance(transport_target, SerialTarget):
        payload = model_to_json(transport_target)
        payload["kind"] = "serial"
        return payload
    if isinstance(transport_target, BluetoothTarget):
        payload = model_to_json(transport_target)
        payload["kind"] = "bluetooth"
        return payload
    return None


def _parse_transport_target(value: object) -> Optional[TransportTarget]:
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
    "PRINTER_CONFIG_SCHEMA_V1",
    "PRINTER_CONFIG_SCHEMA_V2",
    "PrinterConfigParts",
    "parse_printer_config",
    "runtime_settings_from_parts",
    "serialize_printer_config",
]
