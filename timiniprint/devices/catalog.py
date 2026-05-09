from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..protocol.families import (
    get_protocol_behavior,
    get_protocol_definition,
    protocol_requires_speed,
)
from ..protocol.family import ProtocolFamily
from ..protocol.types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..raster import PixelFormat
from .device import (
    BluetoothEndpoint,
    BluetoothEndpointTransport,
    BluetoothTarget,
    PrinterDevice,
    SerialTarget,
    TransportTarget,
)
from .profiles import (
    DetectionNormalizer,
    DetectionRule,
    LevelProfile,
    ModeLevelProfile,
    PrinterProfile,
    SpeedProfile,
    StreamProfile,
)

PROFILE_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "printer_profiles.json"
RULE_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_detection_rules.json")
DEVICE_CONFIG_SCHEMA = "timiniprint/device-config/v1"
_UNSET = object()


def _parse_image_pipeline(entry: Mapping[str, object]) -> ImagePipelineConfig:
    formats_value = entry.get("formats")
    encoding_value = entry.get("encoding")
    if not isinstance(formats_value, list) or not formats_value:
        raise ValueError("Image pipeline formats must be a non-empty JSON array")
    if not encoding_value:
        raise ValueError("Image pipeline encoding is required")
    return ImagePipelineConfig(
        formats=tuple(PixelFormat(str(value)) for value in formats_value),
        encoding=ImageEncoding(str(encoding_value)),
    )


def _family_default_image_pipeline(protocol_family: ProtocolFamily) -> ImagePipelineConfig:
    return get_protocol_definition(protocol_family).behavior.default_image_pipeline


class PrinterCatalog:
    """Load printer profiles and detect runtime devices from catalog data."""

    _cache: Dict[Tuple[Path, Path], "PrinterCatalog"] = {}

    def __init__(self, profiles: Iterable[PrinterProfile], rules: Iterable[DetectionRule]) -> None:
        self._profiles = list(profiles)
        self._rules = list(rules)
        self._profile_by_key = {profile.profile_key: profile for profile in self._profiles}
        self._rule_by_key = {rule.rule_key: rule for rule in self._rules}
        self._validate_speed_requirements()
        self._validate_default_paper_modes()

    @classmethod
    def load(
        cls,
        profile_path: Path = PROFILE_DATA_PATH,
        rule_path: Path = RULE_DATA_PATH,
    ) -> "PrinterCatalog":
        """Load the shared catalog instance from JSON profile and rule files."""
        cache_key = (profile_path, rule_path)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached
        profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
        rules_raw = json.loads(rule_path.read_text(encoding="utf-8"))
        if not isinstance(profiles_raw, list):
            raise ValueError("Profile file must contain a JSON list")
        if not isinstance(rules_raw, list):
            raise ValueError("Detection rule file must contain a JSON list")
        profiles = [cls._parse_profile(entry) for entry in profiles_raw]
        rules = [cls._parse_rule(entry) for entry in rules_raw]
        catalog = cls(profiles, rules)
        cls._cache[cache_key] = catalog
        return catalog

    @staticmethod
    def _parse_level_profile(payload: Mapping[str, object]) -> LevelProfile:
        return LevelProfile(
            low=int(payload["low"]),
            middle=int(payload["middle"]),
            high=int(payload["high"]),
        )

    @classmethod
    def _parse_mode_profile(cls, payload: Mapping[str, object]) -> ModeLevelProfile:
        return ModeLevelProfile(
            image=cls._parse_level_profile(payload["image"]),
            text=cls._parse_level_profile(payload["text"]),
        )

    @classmethod
    def _parse_profile(cls, entry: Mapping[str, object]) -> PrinterProfile:
        stream_payload = entry["stream"]
        tuning_payload = entry["tuning"]
        speed_payload = tuning_payload.get("speed")
        density_payload = tuning_payload.get("density")
        profile = PrinterProfile(
            profile_key=str(entry["profile_key"]),
            size=int(entry["size"]),
            paper_size=int(entry["paper_size"]),
            print_size=int(entry["print_size"]),
            one_length=int(entry["one_length"]),
            dev_dpi=int(entry["dev_dpi"]),
            can_change_mtu=bool(entry["can_change_mtu"]),
            has_id=bool(entry["has_id"]),
            use_spp=bool(entry["use_spp"]),
            can_print_label=bool(entry["can_print_label"]),
            label_value=str(entry["label_value"]),
            back_paper_num=int(entry["back_paper_num"]),
            default_protocol_family=ProtocolFamily.from_value(entry["default_protocol_family"]),
            default_protocol_variant=(
                None
                if entry.get("default_protocol_variant") in (None, "")
                else str(entry["default_protocol_variant"])
            ),
            default_paper_mode=(
                None
                if entry.get("default_paper_mode") in (None, "")
                else PaperMode(str(entry["default_paper_mode"]))
            ),
            default_image_pipeline=_parse_image_pipeline(entry["default_image_pipeline"]),
            stream=StreamProfile(
                chunk_size=int(stream_payload["chunk_size"]),
                delay_ms=int(stream_payload["delay_ms"]),
            ),
            speed=(
                None
                if speed_payload is None
                else SpeedProfile(
                    image=int(speed_payload["image"]),
                    text=int(speed_payload["text"]),
                )
            ),
            energy=cls._parse_mode_profile(tuning_payload["energy"]),
            density=None if density_payload is None else cls._parse_mode_profile(density_payload),
            post_print_feed_count=int(entry.get("post_print_feed_count", 2)),
            a4xii=bool(entry.get("a4xii", False)),
            add_mor_pix=None if entry.get("add_mor_pix") is None else bool(entry.get("add_mor_pix")),
        )
        if profile.stream.chunk_size <= 0:
            raise ValueError(f"Profile {profile.profile_key} has invalid stream.chunk_size")
        if profile.stream.delay_ms < 0:
            raise ValueError(f"Profile {profile.profile_key} has invalid stream.delay_ms")
        return profile

    def _validate_speed_requirements(self) -> None:
        for profile in self._profiles:
            self._validate_profile_speed_for_family(
                profile=profile,
                protocol_family=profile.default_protocol_family,
                context=f"profile {profile.profile_key} default family",
            )
        for rule in self._rules:
            profile = self._profile_by_key.get(rule.profile_key)
            if profile is None:
                continue
            self._validate_profile_speed_for_family(
                profile=profile,
                protocol_family=rule.protocol_family,
                context=f"rule {rule.rule_key}",
            )

    @staticmethod
    def _validate_profile_speed_for_family(
        *,
        profile: PrinterProfile,
        protocol_family: ProtocolFamily,
        context: str,
    ) -> None:
        if profile.speed is not None:
            return
        if not protocol_requires_speed(protocol_family):
            return
        raise ValueError(
            f"{context} requires speed tuning, but profile {profile.profile_key} does not define it"
        )

    def _validate_default_paper_modes(self) -> None:
        for profile in self._profiles:
            if profile.default_paper_mode is None:
                continue
            behavior = get_protocol_behavior(profile.default_protocol_family)
            if behavior.supported_paper_modes_resolver is not None:
                supported_modes = behavior.supported_paper_modes_resolver(
                    profile.default_protocol_variant
                )
            else:
                supported_modes = behavior.supported_paper_modes
            if profile.default_paper_mode not in supported_modes:
                raise ValueError(
                    f"profile {profile.profile_key} default_paper_mode "
                    f"{profile.default_paper_mode.value} is not supported by "
                    f"{profile.default_protocol_family.value}"
                )

    @staticmethod
    def _parse_rule(entry: Mapping[str, object]) -> DetectionRule:
        prefixes_value = entry.get("prefixes")
        exact_names_value = entry.get("exact_names", [])
        if prefixes_value is None:
            prefixes_value = []
        if not isinstance(prefixes_value, list):
            raise ValueError("Detection rule prefixes must be a JSON array")
        if not isinstance(exact_names_value, list):
            raise ValueError("Detection rule exact_names must be a JSON array")
        if not prefixes_value and not exact_names_value:
            raise ValueError("Detection rule requires at least one prefix or exact_name")
        image_pipeline_value = entry.get("image_pipeline")
        return DetectionRule(
            rule_key=str(entry["rule_key"]),
            prefixes=tuple(DetectionNormalizer.normalize_name(str(value)) for value in prefixes_value),
            exact_names=tuple(DetectionNormalizer.normalize_name(str(value)) for value in exact_names_value),
            profile_key=str(entry["profile_key"]),
            protocol_family=ProtocolFamily.from_value(entry["protocol_family"]),
            protocol_variant=(
                None
                if entry.get("protocol_variant") in (None, "")
                else str(entry["protocol_variant"])
            ),
            mac_suffixes=tuple(str(value).upper() for value in entry.get("mac_suffixes", [])),
            image_pipeline=None
            if image_pipeline_value is None
            else _parse_image_pipeline(image_pipeline_value),
            runtime_variant=None if entry.get("runtime_variant") is None else str(entry["runtime_variant"]),
            runtime_density_profile_key=None
            if entry.get("runtime_density_profile_key") is None
            else str(entry["runtime_density_profile_key"]),
        )

    @property
    def profiles(self) -> List[PrinterProfile]:
        return list(sorted(self._profiles, key=lambda profile: profile.profile_key))

    @property
    def rules(self) -> List[DetectionRule]:
        return list(self._rules)

    def get_profile(self, profile_key: str) -> Optional[PrinterProfile]:
        """Return a profile by key, or ``None`` if the key is unknown."""
        return self._profile_by_key.get(profile_key)

    def require_profile(self, profile_key: str) -> PrinterProfile:
        """Return a profile by key, raising when the key is unknown."""
        profile = self.get_profile(profile_key)
        if profile is None:
            raise RuntimeError(f"Unknown printer profile '{profile_key}'")
        return profile

    def detect_device(
        self,
        device_name: str,
        address: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> Optional[PrinterDevice]:
        """Detect a ``PrinterDevice`` from a known name and optional address.

        This is catalog-level detection only. It does not scan hardware.
        """
        match = self._detect_rule_match(device_name, address)
        if match is None:
            return None
        rule, profile = match
        return self._build_device(
            display_name=device_name,
            profile=profile,
            protocol_family=rule.protocol_family,
            protocol_variant=rule.protocol_variant,
            image_pipeline=self._select_image_pipeline(profile, rule),
            runtime_variant=rule.runtime_variant,
            runtime_density_profile_key=rule.runtime_density_profile_key,
            detection_rule_key=rule.rule_key,
            transport_target=transport_target,
        )

    def device_from_profile(
        self,
        profile_key: str,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        """Create a runtime device directly from a known profile key."""
        profile = self.require_profile(profile_key)
        return self._build_device(
            display_name=display_name or profile.profile_key,
            profile=profile,
            protocol_family=profile.default_protocol_family,
            protocol_variant=profile.default_protocol_variant,
            image_pipeline=profile.default_image_pipeline,
            runtime_variant=None,
            runtime_density_profile_key=None,
            detection_rule_key=f"manual:{profile.profile_key}",
            transport_target=transport_target,
        )

    def serialize_device_config(self, device: PrinterDevice) -> dict[str, Any]:
        """Serialize a resolved device into a JSON-ready config object."""
        return {
            "schema": DEVICE_CONFIG_SCHEMA,
            "display_name": device.display_name,
            "profile_key": device.profile.profile_key,
            "protocol_family": device.protocol_family.value,
            "protocol_variant": device.protocol_variant,
            "image_pipeline": {
                "formats": [pixel_format.value for pixel_format in device.image_pipeline.formats],
                "encoding": device.image_pipeline.encoding.value,
            },
            "runtime_variant": device.runtime_variant,
            "runtime_density_profile_key": (
                None
                if device.runtime_density_profile is None
                else device.runtime_density_profile.profile_key
            ),
            "transport_target": self._serialize_transport_target(device.transport_target),
        }

    def device_from_config(
        self,
        config: Mapping[str, object],
        *,
        transport_target: TransportTarget | None | object = _UNSET,
        display_name: Optional[str] = None,
    ) -> PrinterDevice:
        """Rebuild a runtime device from a serialized device config."""
        schema = str(config.get("schema") or "")
        if schema != DEVICE_CONFIG_SCHEMA:
            raise RuntimeError(
                f"Unsupported device config schema '{schema or '<missing>'}'"
            )
        profile = self.require_profile(str(config["profile_key"]))
        image_pipeline_entry = config.get("image_pipeline")
        if not isinstance(image_pipeline_entry, Mapping):
            raise RuntimeError("Device config is missing image_pipeline")
        resolved_transport_target = (
            self._parse_transport_target(config.get("transport_target"))
            if transport_target is _UNSET
            else transport_target
        )
        return self._build_device(
            display_name=display_name or str(config.get("display_name") or profile.profile_key),
            profile=profile,
            protocol_family=ProtocolFamily.from_value(str(config["protocol_family"])),
            protocol_variant=(
                profile.default_protocol_variant
                if config.get("protocol_variant") in (None, "")
                else str(config["protocol_variant"])
            ),
            image_pipeline=_parse_image_pipeline(image_pipeline_entry),
            runtime_variant=(
                None
                if config.get("runtime_variant") in (None, "")
                else str(config["runtime_variant"])
            ),
            runtime_density_profile_key=(
                None
                if config.get("runtime_density_profile_key") in (None, "")
                else str(config["runtime_density_profile_key"])
            ),
            detection_rule_key=f"config:{profile.profile_key}",
            transport_target=resolved_transport_target,
        )

    def _detect_rule_match(
        self,
        device_name: str,
        address: Optional[str] = None,
    ) -> tuple[DetectionRule, PrinterProfile] | None:
        for case_sensitive in (True, False):
            for rule in self._rules:
                if not rule.matches(device_name, address, case_sensitive=case_sensitive):
                    continue
                profile = self._profile_by_key.get(rule.profile_key)
                if profile is None:
                    raise ValueError(
                        f"Detection rule {rule.rule_key} references unknown profile {rule.profile_key}"
                    )
                return rule, profile
        return None

    def _build_device(
        self,
        *,
        display_name: str,
        profile: PrinterProfile,
        protocol_family: ProtocolFamily,
        protocol_variant: str | None,
        image_pipeline: ImagePipelineConfig,
        runtime_variant: str | None,
        runtime_density_profile_key: str | None,
        detection_rule_key: str,
        transport_target: TransportTarget | None,
    ) -> PrinterDevice:
        runtime_density_profile = (
            None
            if runtime_density_profile_key is None
            else self.require_profile(runtime_density_profile_key)
        )
        resolved_protocol_variant = (
            profile.default_protocol_variant if protocol_variant is None else protocol_variant
        )
        self._validate_protocol_variant(protocol_family, resolved_protocol_variant)
        self._validate_profile_speed_for_family(
            profile=profile,
            protocol_family=protocol_family,
            context=f"device {display_name}",
        )
        return PrinterDevice(
            display_name=display_name,
            profile=profile,
            protocol_family=protocol_family,
            protocol_variant=resolved_protocol_variant,
            image_pipeline=image_pipeline,
            runtime_variant=runtime_variant,
            runtime_density_profile=runtime_density_profile,
            transport_target=transport_target,
            detection_rule_key=detection_rule_key,
        )

    @staticmethod
    def _validate_protocol_variant(
        protocol_family: ProtocolFamily,
        protocol_variant: str | None,
    ) -> None:
        if protocol_variant in (None, ""):
            return
        supported_variants = get_protocol_behavior(protocol_family).supported_protocol_variants
        if protocol_variant not in supported_variants:
            raise RuntimeError(
                f"{protocol_family.value} does not support protocol variant {protocol_variant!r}"
            )

    @staticmethod
    def _select_image_pipeline(
        profile: PrinterProfile,
        rule: DetectionRule,
    ) -> ImagePipelineConfig:
        if rule.image_pipeline is not None:
            return rule.image_pipeline
        if rule.protocol_family == profile.default_protocol_family:
            return profile.default_image_pipeline
        return _family_default_image_pipeline(rule.protocol_family)

    @staticmethod
    def _serialize_transport_target(
        transport_target: TransportTarget | None,
    ) -> dict[str, Any] | None:
        if isinstance(transport_target, SerialTarget):
            return {
                "kind": "serial",
                "path": transport_target.path,
                "baud_rate": transport_target.baud_rate,
            }
        if isinstance(transport_target, BluetoothTarget):
            return {
                "kind": "bluetooth",
                "display_address": transport_target.display_address,
                "transport_badge": transport_target.transport_badge,
                "classic_endpoint": PrinterCatalog._serialize_bluetooth_endpoint(
                    transport_target.classic_endpoint
                ),
                "ble_endpoint": PrinterCatalog._serialize_bluetooth_endpoint(
                    transport_target.ble_endpoint
                ),
            }
        return None

    @staticmethod
    def _serialize_bluetooth_endpoint(
        endpoint: BluetoothEndpoint | None,
    ) -> dict[str, Any] | None:
        if endpoint is None:
            return None
        return {
            "name": endpoint.name,
            "address": endpoint.address,
            "paired": endpoint.paired,
            "transport": endpoint.transport.value,
        }

    @staticmethod
    def _parse_transport_target(value: object) -> TransportTarget | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise RuntimeError("Invalid transport_target in device config")
        kind = str(value.get("kind") or "")
        if kind == "serial":
            path = str(value.get("path") or "")
            if not path:
                raise RuntimeError("Serial device config is missing path")
            return SerialTarget(
                path=path,
                baud_rate=int(value.get("baud_rate") or 115200),
            )
        if kind == "bluetooth":
            display_address = str(value.get("display_address") or "")
            if not display_address:
                raise RuntimeError("Bluetooth device config is missing display_address")
            return BluetoothTarget(
                classic_endpoint=PrinterCatalog._parse_bluetooth_endpoint(
                    value.get("classic_endpoint")
                ),
                ble_endpoint=PrinterCatalog._parse_bluetooth_endpoint(
                    value.get("ble_endpoint")
                ),
                display_address=display_address,
                transport_badge=str(value.get("transport_badge") or ""),
            )
        raise RuntimeError(f"Unsupported transport_target kind '{kind or '<missing>'}'")

    @staticmethod
    def _parse_bluetooth_endpoint(value: object) -> BluetoothEndpoint | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise RuntimeError("Invalid bluetooth endpoint in device config")
        address = str(value.get("address") or "")
        if not address:
            raise RuntimeError("Bluetooth endpoint is missing address")
        return BluetoothEndpoint(
            name=str(value.get("name") or ""),
            address=address,
            paired=value.get("paired"),
            transport=BluetoothEndpointTransport(
                str(value.get("transport") or BluetoothEndpointTransport.CLASSIC.value)
            ),
        )


__all__ = [
    "DEVICE_CONFIG_SCHEMA",
    "PROFILE_DATA_PATH",
    "RULE_DATA_PATH",
    "PrinterCatalog",
]
