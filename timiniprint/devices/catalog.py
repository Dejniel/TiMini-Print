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
from ..protocol.types import ImagePipelineConfig
from .printer_config import (
    parse_printer_config,
    runtime_settings_from_parts,
    serialize_printer_config,
)
from .device import (
    PrinterDevice,
    TransportTarget,
)
from .model_codec import model_from_json
from .profiles import (
    DetectionRule,
    PrinterProfile,
    PrinterRuntimeDefaults,
    RuntimeSettings,
)

PROFILE_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "printer_profiles.json"
RULE_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_detection_rules.json")
PRINTER_RUNTIME_DEFAULTS_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_runtime_defaults.json")
_UNSET = object()


class PrinterCatalog:
    """Load printer profiles and detect runtime devices from catalog data."""

    _cache: Dict[Tuple[Path, Path, Path], "PrinterCatalog"] = {}

    def __init__(
        self,
        profiles: Iterable[PrinterProfile],
        rules: Iterable[DetectionRule],
        printer_runtime_defaults: Iterable[PrinterRuntimeDefaults] = (),
    ) -> None:
        self._profiles = list(profiles)
        self._rules = list(rules)
        self._printer_runtime_defaults = list(printer_runtime_defaults)
        self._profile_by_key = {profile.profile_key: profile for profile in self._profiles}
        self._rule_by_key = {rule.rule_key: rule for rule in self._rules}
        self._runtime_defaults_by_key = {
            defaults.key: defaults for defaults in self._printer_runtime_defaults
        }
        self._validate_speed_requirements()
        self._validate_default_paper_modes()
        self._validate_runtime_defaults_keys()

    @classmethod
    def load(
        cls,
        profile_path: Path = PROFILE_DATA_PATH,
        rule_path: Path = RULE_DATA_PATH,
        runtime_defaults_path: Path = PRINTER_RUNTIME_DEFAULTS_DATA_PATH,
    ) -> "PrinterCatalog":
        """Load the shared catalog instance from JSON profile and rule files."""
        cache_key = (profile_path, rule_path, runtime_defaults_path)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached
        profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
        rules_raw = json.loads(rule_path.read_text(encoding="utf-8"))
        printer_runtime_defaults_raw = json.loads(runtime_defaults_path.read_text(encoding="utf-8"))
        if not isinstance(profiles_raw, list):
            raise ValueError("Profile file must contain a JSON list")
        if not isinstance(rules_raw, list):
            raise ValueError("Detection rule file must contain a JSON list")
        if not isinstance(printer_runtime_defaults_raw, list):
            raise ValueError("Runtime defaults file must contain a JSON list")
        profiles = [model_from_json(PrinterProfile, entry) for entry in profiles_raw]
        rules = [model_from_json(DetectionRule, entry) for entry in rules_raw]
        printer_runtime_defaults = [
            model_from_json(PrinterRuntimeDefaults, entry)
            for entry in printer_runtime_defaults_raw
        ]
        catalog = cls(
            profiles,
            rules,
            printer_runtime_defaults,
        )
        cls._cache[cache_key] = catalog
        return catalog

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
            f"{context} requires speed defaults, but profile {profile.profile_key} does not define it"
        )

    def _validate_default_paper_modes(self) -> None:
        for profile in self._profiles:
            self._validate_profile_default_paper_mode(profile)

    def _validate_runtime_defaults_keys(self) -> None:
        colliding_keys = sorted(
            set(self._profile_by_key) & set(self._runtime_defaults_by_key)
        )
        if colliding_keys:
            raise ValueError(
                "Runtime defaults keys collide with profile keys: "
                + ", ".join(colliding_keys)
            )
        for defaults in self._printer_runtime_defaults:
            if defaults.profile_key not in self._profile_by_key:
                raise ValueError(
                    f"Runtime defaults {defaults.key} references unknown profile "
                    f"{defaults.profile_key}"
                )
        for rule in self._rules:
            if rule.runtime_defaults_key is None:
                continue
            if rule.runtime_defaults_key not in self._runtime_defaults_by_key:
                raise ValueError(
                    f"Detection rule {rule.rule_key} references unknown runtime defaults "
                    f"{rule.runtime_defaults_key}"
                )

    @staticmethod
    def _validate_profile_default_paper_mode(profile: PrinterProfile) -> None:
        if profile.default_paper_mode is None:
            return
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
        defaults = (
            None
            if rule.runtime_defaults_key is None
            else self.require_runtime_defaults(rule.runtime_defaults_key)
        )
        return self._build_device(
            display_name=device_name,
            profile=profile,
            protocol_family=rule.protocol_family,
            protocol_variant=rule.protocol_variant,
            image_pipeline=self._select_image_pipeline(profile, rule),
            runtime_settings=runtime_settings_from_parts(
                variant=rule.runtime_variant,
                defaults=defaults,
            ),
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
            runtime_settings=None,
            detection_rule_key=f"manual:{profile.profile_key}",
            transport_target=transport_target,
        )

    def device_from_key(
        self,
        key: str,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        """Create a runtime device from a profile key or runtime defaults key."""
        if self.get_profile(key) is not None:
            return self.device_from_profile(
                key,
                display_name=display_name,
                transport_target=transport_target,
            )
        if self.get_runtime_defaults(key) is not None:
            return self.device_from_runtime_defaults(
                key,
                display_name=display_name,
                transport_target=transport_target,
            )
        raise RuntimeError(f"Unknown profile/runtime defaults key '{key}'")

    def device_from_runtime_defaults(
        self,
        runtime_defaults_key: str,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        """Create a runtime device from a known runtime defaults key."""
        defaults = self.require_runtime_defaults(runtime_defaults_key)
        profile = self.require_profile(defaults.profile_key)
        runtime_settings = runtime_settings_from_parts(
            variant=defaults.variant,
            defaults=defaults,
        )
        return self._build_device(
            display_name=display_name or runtime_defaults_key,
            profile=profile,
            protocol_family=profile.default_protocol_family,
            protocol_variant=profile.default_protocol_variant,
            image_pipeline=profile.default_image_pipeline,
            runtime_settings=runtime_settings,
            detection_rule_key=f"runtime:{runtime_defaults_key}",
            transport_target=transport_target,
        )

    def serialize_printer_config(self, device: PrinterDevice) -> dict[str, Any]:
        """Serialize a device into an editable printer config object.

        The printer config keeps ``profile_key`` as the catalog fallback and writes the
        full current profile as overrides so users can tune values in-place.
        Removing an override key falls back to the catalog profile value.
        """
        return serialize_printer_config(device)

    def device_from_printer_config(
        self,
        printer_config: Mapping[str, object],
        *,
        transport_target: TransportTarget | None | object = _UNSET,
        display_name: Optional[str] = None,
    ) -> PrinterDevice:
        """Rebuild a runtime device from a serialized printer config."""
        printer_config_parts = parse_printer_config(
            printer_config,
            require_profile=self.require_profile,
            require_runtime_defaults=self.require_runtime_defaults,
        )
        resolved_transport_target = (
            printer_config_parts.transport_target
            if transport_target is _UNSET
            else transport_target
        )
        return self._build_device(
            display_name=display_name or printer_config_parts.display_name,
            profile=printer_config_parts.profile,
            protocol_family=printer_config_parts.profile.default_protocol_family,
            protocol_variant=printer_config_parts.profile.default_protocol_variant,
            image_pipeline=printer_config_parts.profile.default_image_pipeline,
            runtime_settings=printer_config_parts.runtime_settings,
            detection_rule_key=f"printer_config:{printer_config_parts.profile.profile_key}",
            transport_target=resolved_transport_target,
        )

    def require_runtime_defaults(self, runtime_defaults_key: str) -> PrinterRuntimeDefaults:
        defaults = self.get_runtime_defaults(runtime_defaults_key)
        if defaults is None:
            raise RuntimeError(f"Unknown runtime defaults '{runtime_defaults_key}'")
        return defaults

    def get_runtime_defaults(self, runtime_defaults_key: str) -> PrinterRuntimeDefaults | None:
        return self._runtime_defaults_by_key.get(runtime_defaults_key)

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
        runtime_settings: RuntimeSettings | None,
        detection_rule_key: str,
        transport_target: TransportTarget | None,
    ) -> PrinterDevice:
        resolved_protocol_variant = (
            profile.default_protocol_variant if protocol_variant is None else protocol_variant
        )
        self._validate_protocol_variant(protocol_family, resolved_protocol_variant)
        self._validate_profile_speed_for_family(
            profile=profile,
            protocol_family=protocol_family,
            context=f"device {display_name}",
        )
        self._validate_profile_default_paper_mode(profile)
        return PrinterDevice(
            display_name=display_name,
            profile=profile,
            protocol_family=protocol_family,
            protocol_variant=resolved_protocol_variant,
            image_pipeline=image_pipeline,
            runtime_settings=runtime_settings,
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
        return get_protocol_definition(rule.protocol_family).behavior.default_image_pipeline

__all__ = [
    "PROFILE_DATA_PATH",
    "RULE_DATA_PATH",
    "PRINTER_RUNTIME_DEFAULTS_DATA_PATH",
    "PrinterCatalog",
]
