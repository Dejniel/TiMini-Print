from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

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
    DetectionNormalizer,
    ModelMatch,
    NamedModelDetection,
    PaperPreset,
    PrinterProfile,
    RuntimePreset,
    RuntimeSettings,
    SupportedModelMatch,
    SupportedPrinterModel,
    UnsupportedModelMatch,
    UnsupportedPrinterModel,
)

PROFILE_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "printer_profiles.json"
MODEL_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_models.json")
UNSUPPORTED_MODEL_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_models_unsupported.json")
ORIGIN_APP_DATA_PATH = PROFILE_DATA_PATH.with_name("origin_apps.json")
PAPER_PRESET_DATA_PATH = PROFILE_DATA_PATH.with_name("printer_paper_presets.json")
_UNSET = object()


class PrinterCatalog:
    """Load printer profiles and detect runtime devices from catalog data."""

    _cache: Dict[tuple[Path, Path, Path, Path, Path | None], "PrinterCatalog"] = {}

    def __init__(
        self,
        profiles: Iterable[PrinterProfile],
        models: Iterable[SupportedPrinterModel],
        unsupported_models: Iterable[UnsupportedPrinterModel] = (),
        origin_app_names: Mapping[str, str] | None = None,
    ) -> None:
        self._profiles = list(profiles)
        self._models = sorted(
            models,
            key=self._detection_specificity,
            reverse=True,
        )
        self._unsupported_models = sorted(
            unsupported_models,
            key=self._detection_specificity,
            reverse=True,
        )
        self._profile_by_key = {profile.profile_key: profile for profile in self._profiles}
        self._model_by_key = {model.model_key: model for model in self._models}
        self._models_by_detection_name = self._index_models_by_detection_name(self._models)
        self._unsupported_model_by_key = {
            model.model_key: model for model in self._unsupported_models
        }
        self._origin_app_names = dict(origin_app_names or {})
        self._supported_detection_entries = self._sorted_detection_entries(self._models)
        self._unsupported_detection_entries = self._sorted_detection_entries(
            self._unsupported_models
        )
        self._validate_speed_requirements()
        self._validate_paper_presets()
        self._validate_model_keys()
        self._validate_runtime_presets()
        self._validate_unsupported_model_keys()
        self._validate_model_references()
        self._validate_implemented_protocols()
        self._validate_origin_app_names()

    @staticmethod
    def _detection_specificity(model: SupportedPrinterModel | UnsupportedPrinterModel) -> tuple[int, int]:
        triggers: list[str] = []
        for named_detection in model.detections:
            detection = named_detection.detection
            triggers.extend(detection.exact_names)
            triggers.extend(detection.prefixes)
            triggers.extend(detection.mac_suffixes)
        max_trigger_length = max((len(trigger) for trigger in triggers), default=0)
        return (max_trigger_length, len(triggers))

    @staticmethod
    def _named_detection_specificity(detection: NamedModelDetection) -> tuple[int, int, int, int, int, int]:
        name_triggers = [
            *detection.detection.exact_names,
            *detection.detection.prefixes,
        ]
        trigger_lengths = [
            len(trigger[:-1]) if trigger.endswith(("-", "_")) else len(trigger)
            for trigger in name_triggers
        ]
        max_trigger_length = max(trigger_lengths, default=0)
        max_raw_length = max((len(trigger) for trigger in name_triggers), default=0)
        uppercase_score = max(
            (sum(1 for char in trigger if char.isupper()) for trigger in name_triggers),
            default=0,
        )
        return (
            max_trigger_length,
            int(bool(detection.detection.mac_suffixes)),
            int(bool(detection.detection.exact_names)),
            max_raw_length,
            uppercase_score,
            len(name_triggers),
        )

    @staticmethod
    def _trigger_specificity(
        trigger: str,
        *,
        exact: bool,
        has_mac_suffix: bool,
    ) -> tuple[int, int, int, int, int]:
        trigger_length = len(trigger[:-1]) if trigger.endswith(("-", "_")) else len(trigger)
        return (
            trigger_length,
            int(has_mac_suffix),
            int(exact),
            len(trigger),
            sum(1 for char in trigger if char.isupper()),
        )

    @classmethod
    def _matched_detection_specificity(
        cls,
        detection: NamedModelDetection,
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool,
    ) -> tuple[int, int, int, int, int] | None:
        model_detection = detection.detection
        has_mac_suffix = bool(model_detection.mac_suffixes)
        if has_mac_suffix:
            if not address or not DetectionNormalizer.is_mac_like_address(address):
                return None
            normalized = DetectionNormalizer.normalize_mac_candidate(address)
            if not any(normalized.endswith(suffix) for suffix in model_detection.mac_suffixes):
                return None

        normalized_name = DetectionNormalizer.normalize_name(device_name)
        folded_name = DetectionNormalizer.fold_name(device_name)
        matched: list[tuple[int, int, int, int, int]] = []
        for exact_name in model_detection.exact_names:
            candidate = exact_name if case_sensitive else DetectionNormalizer.fold_name(exact_name)
            if (normalized_name if case_sensitive else folded_name) == candidate:
                matched.append(
                    cls._trigger_specificity(
                        exact_name,
                        exact=True,
                        has_mac_suffix=has_mac_suffix,
                    )
                )
        for prefix in model_detection.prefixes:
            candidate = prefix if case_sensitive else DetectionNormalizer.fold_name(prefix)
            if (normalized_name if case_sensitive else folded_name).startswith(candidate):
                matched.append(
                    cls._trigger_specificity(
                        prefix,
                        exact=False,
                        has_mac_suffix=has_mac_suffix,
                    )
                )
        return max(matched, default=None)

    @classmethod
    def _sorted_detection_entries(
        cls,
        models: Iterable[SupportedPrinterModel] | Iterable[UnsupportedPrinterModel],
    ) -> list[tuple[SupportedPrinterModel | UnsupportedPrinterModel, NamedModelDetection]]:
        entries = [
            (model, detection)
            for model in models
            for detection in model.detections
        ]
        return sorted(
            entries,
            key=lambda entry: cls._named_detection_specificity(entry[1]),
            reverse=True,
        )

    @staticmethod
    def _index_models_by_detection_name(
        models: Iterable[SupportedPrinterModel],
    ) -> dict[str, tuple[SupportedPrinterModel, ...]]:
        indexed: dict[str, list[SupportedPrinterModel]] = {}
        for model in models:
            for detection in model.detections:
                name = detection.normalized_name
                entries = indexed.setdefault(name, [])
                if model not in entries:
                    entries.append(model)
        return {
            name: tuple(models)
            for name, models in indexed.items()
        }

    @classmethod
    def load(
        cls,
        profile_path: Path = PROFILE_DATA_PATH,
        model_path: Path = MODEL_DATA_PATH,
        unsupported_model_path: Path = UNSUPPORTED_MODEL_DATA_PATH,
        paper_preset_path: Path = PAPER_PRESET_DATA_PATH,
        origin_app_path: Path | None = ORIGIN_APP_DATA_PATH,
    ) -> "PrinterCatalog":
        """Load the shared catalog instance from JSON profile and model files."""
        cache_key = (
            profile_path,
            model_path,
            unsupported_model_path,
            paper_preset_path,
            origin_app_path,
        )
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached
        profiles_raw = json.loads(profile_path.read_text(encoding="utf-8"))
        models_raw = json.loads(model_path.read_text(encoding="utf-8"))
        unsupported_models_raw = json.loads(unsupported_model_path.read_text(encoding="utf-8"))
        paper_presets_raw = json.loads(paper_preset_path.read_text(encoding="utf-8"))
        origin_app_names_raw = (
            {}
            if origin_app_path is None
            else json.loads(origin_app_path.read_text(encoding="utf-8"))
        )
        if not isinstance(profiles_raw, list):
            raise ValueError("Profile file must contain a JSON list")
        if not isinstance(models_raw, list):
            raise ValueError("Model file must contain a JSON list")
        if not isinstance(unsupported_models_raw, list):
            raise ValueError("Unsupported model file must contain a JSON list")
        if not isinstance(paper_presets_raw, dict):
            raise ValueError("Paper preset file must contain a JSON object")
        if not isinstance(origin_app_names_raw, dict):
            raise ValueError("Origin app file must contain a JSON object")
        paper_presets = cls._load_paper_presets(paper_presets_raw)
        profiles = [
            model_from_json(
                PrinterProfile,
                cls._resolve_profile_paper_presets(entry, paper_presets),
            )
            for entry in profiles_raw
        ]
        models = [model_from_json(SupportedPrinterModel, entry) for entry in models_raw]
        unsupported_models = [
            model_from_json(UnsupportedPrinterModel, entry)
            for entry in unsupported_models_raw
        ]
        origin_app_names = {
            str(package): str(name)
            for package, name in origin_app_names_raw.items()
        }
        catalog = cls(
            profiles,
            models,
            unsupported_models,
            origin_app_names,
        )
        cls._cache[cache_key] = catalog
        return catalog

    @staticmethod
    def _load_paper_presets(raw: Mapping[str, object]) -> dict[str, dict[str, object]]:
        presets: dict[str, dict[str, object]] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"Paper preset {key} must be an object")
            payload = dict(value)
            payload["key"] = key
            # Validate once here; profiles get independent instances below.
            model_from_json(PaperPreset, payload, path=f"$.{key}")
            presets[str(key)] = payload
        return presets

    @staticmethod
    def _resolve_profile_paper_presets(
        entry: object,
        paper_presets: Mapping[str, Mapping[str, object]],
    ) -> object:
        if not isinstance(entry, Mapping):
            return entry
        payload = dict(entry)
        references = payload.get("paper_presets")
        if not isinstance(references, list):
            raise ValueError(f"profile {payload.get('profile_key', '<unknown>')} paper_presets must be an array")
        resolved = []
        for reference in references:
            if not isinstance(reference, str):
                raise ValueError(
                    f"profile {payload.get('profile_key', '<unknown>')} paper_presets must contain preset keys"
                )
            preset = paper_presets.get(reference)
            if preset is None:
                raise ValueError(
                    f"profile {payload.get('profile_key', '<unknown>')} references unknown paper preset {reference!r}"
                )
            resolved_preset = dict(preset)
            resolved_preset["key"] = reference
            resolved.append(resolved_preset)
        payload["paper_presets"] = resolved
        return payload

    def _validate_speed_requirements(self) -> None:
        for profile in self._profiles:
            self._validate_profile_speed_for_family(
                profile=profile,
                protocol_family=profile.protocol_default.type,
                context=f"profile {profile.profile_key} default family",
            )
        for model in self._models:
            profile = self._profile_by_key.get(model.profile_key)
            if profile is None:
                continue
            protocol_family = self._protocol_family_for_model(model, profile)
            self._validate_profile_speed_for_family(
                profile=profile,
                protocol_family=protocol_family,
                context=f"model {model.model_key}",
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

    def _validate_paper_presets(self) -> None:
        for profile in self._profiles:
            self._validate_profile_paper_presets(profile)

    def _validate_model_keys(self) -> None:
        duplicate_keys = self._duplicate_keys(model.model_key for model in self._models)
        if duplicate_keys:
            raise ValueError(
                "Duplicate printer model keys: "
                + ", ".join(duplicate_keys)
            )

    def _validate_unsupported_model_keys(self) -> None:
        duplicate_keys = self._duplicate_keys(
            model.model_key for model in self._unsupported_models
        )
        if duplicate_keys:
            raise ValueError(
                "Duplicate unsupported model keys: "
                + ", ".join(duplicate_keys)
            )
        colliding_keys = sorted(set(self._unsupported_model_by_key) & set(self._model_by_key))
        if colliding_keys:
            raise ValueError(
                "Unsupported model keys collide with supported model keys: "
                + ", ".join(colliding_keys)
            )

    def _validate_runtime_presets(self) -> None:
        for profile in self._profiles:
            seen_in_profile: set[str] = set()
            for preset in profile.runtime_presets:
                if preset.key in seen_in_profile:
                    raise ValueError(
                        f"Profile {profile.profile_key} repeats protocol runtime preset "
                        f"{preset.key}"
                    )
                seen_in_profile.add(preset.key)

    def _validate_model_references(self) -> None:
        for model in self._models:
            profile = self._profile_by_key.get(model.profile_key)
            if profile is None:
                raise ValueError(
                    f"Printer model {model.model_key} references unknown profile "
                    f"{model.profile_key}"
                )
            if model.profile_runtime_preset_key is None:
                continue
            if not any(
                preset.key == model.profile_runtime_preset_key
                for preset in profile.runtime_presets
            ):
                raise ValueError(
                    f"Printer model {model.model_key} references unknown runtime preset "
                    f"{model.profile_runtime_preset_key} for profile {profile.profile_key}"
                )

    def _validate_implemented_protocols(self) -> None:
        for model in self._models:
            profile = self._profile_by_key[model.profile_key]
            protocol_family = self._protocol_family_for_model(model, profile)
            if get_protocol_behavior(protocol_family).implemented:
                continue
            raise ValueError(
                f"Supported printer model {model.model_key} uses unimplemented "
                f"protocol family {protocol_family.value}"
            )

    def _validate_origin_app_names(self) -> None:
        if not self._origin_app_names:
            return
        known_packages = {
            package
            for model in [*self._models, *self._unsupported_models]
            for package in model.origin_app_packages
        }
        missing = sorted(known_packages - set(self._origin_app_names))
        if missing:
            raise ValueError(
                "Origin app names are missing packages: "
                + ", ".join(missing)
            )

    @staticmethod
    def _duplicate_keys(keys: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for key in keys:
            if key in seen:
                duplicates.add(key)
            seen.add(key)
        return sorted(duplicates)

    @staticmethod
    def _validate_profile_paper_presets(profile: PrinterProfile) -> None:
        paper_modes = {
            preset.paper_mode
            for preset in profile.paper_presets
            if preset.paper_mode is not None
        }
        if not paper_modes:
            return
        supported_modes = PrinterCatalog._profile_supported_paper_modes(profile)
        unsupported_modes = sorted(
            mode.value
            for mode in paper_modes
            if mode not in supported_modes
        )
        if unsupported_modes:
            raise ValueError(
                f"profile {profile.profile_key} paper presets use unsupported paper mode(s): "
                + ", ".join(unsupported_modes)
            )

    @staticmethod
    def _profile_supported_paper_modes(profile: PrinterProfile):
        behavior = get_protocol_behavior(profile.protocol_default.type)
        if behavior.supported_paper_modes_resolver is not None:
            return behavior.supported_paper_modes_resolver(
                profile.protocol_default.packets_type
            )
        return behavior.supported_paper_modes

    @property
    def profiles(self) -> List[PrinterProfile]:
        return list(sorted(self._profiles, key=lambda profile: profile.profile_key))

    @property
    def models(self) -> List[SupportedPrinterModel]:
        return list(self._models)

    @property
    def unsupported_models(self) -> List[UnsupportedPrinterModel]:
        return list(self._unsupported_models)

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
        """Detect a printable ``PrinterDevice`` from a known name and address.

        This is catalog-level detection only; it does not scan hardware.
        More specific unsupported metadata can prevent a broad supported prefix
        from stealing an unrelated model. When supported and unsupported matches
        have the same specificity, supported wins. If multiple supported candidates
        tie, this returns ``None`` so callers
        can ask the user to choose the source app/model explicitly.
        """
        supported = [
            match
            for match in self.detect_model(device_name, address)
            if isinstance(match, SupportedModelMatch)
        ]
        if len(supported) != 1:
            return None
        return self.device_from_match(
            supported[0],
            display_name=device_name,
            transport_target=transport_target,
        )

    def device_from_match(
        self,
        match: SupportedModelMatch,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        """Create a runtime device from a supported catalog match."""
        model = match.model
        profile = match.profile
        return self._build_device(
            display_name=display_name or match.detection.name,
            profile=profile,
            protocol_family=self._protocol_family_for_model(model, profile),
            protocol_variant=self._protocol_packets_type_for_model(model, profile),
            image_pipeline=self._select_image_pipeline(profile, model),
            runtime_settings=self._runtime_settings_for_model(model, profile),
            model_key=model.model_key,
            origin_app_packages=model.origin_app_packages,
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
            protocol_family=profile.protocol_default.type,
            protocol_variant=profile.protocol_default.packets_type,
            image_pipeline=profile.default_image_pipeline,
            runtime_settings=None,
            model_key=f"manual:{profile.profile_key}",
            origin_app_packages=(),
            transport_target=transport_target,
        )

    def device_from_key(
        self,
        key: str,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        """Create a runtime device from a model key or public detection name."""
        model = self.get_model(key)
        if model is not None:
            return self.device_from_model(
                key,
                display_name=display_name,
                transport_target=transport_target,
            )
        matches = self.get_models_by_detection_name(key)
        if len(matches) == 1:
            return self.device_from_model(
                matches[0].model_key,
                display_name=display_name or key,
                transport_target=transport_target,
            )
        if len(matches) > 1:
            candidates = ", ".join(self._format_model_candidate(model) for model in matches)
            raise RuntimeError(
                f"Printer name '{key}' is ambiguous; choose the original app and use one of these model keys: "
                f"{candidates}"
            )
        raise RuntimeError(f"Unknown printer model or detection name '{key}'")

    def get_model(self, model_key: str) -> SupportedPrinterModel | None:
        return self._model_by_key.get(model_key)

    def get_models_by_detection_name(self, name: str) -> tuple[SupportedPrinterModel, ...]:
        normalized = NamedModelDetection.normalize_public_name(name)
        return self._models_by_detection_name.get(normalized, ())

    def require_model(self, model_key: str) -> SupportedPrinterModel:
        model = self.get_model(model_key)
        if model is None:
            raise RuntimeError(f"Unknown printer model '{model_key}'")
        return model

    def get_unsupported_model(self, model_key: str) -> UnsupportedPrinterModel | None:
        return self._unsupported_model_by_key.get(model_key)

    def origin_app_names(self, packages: Iterable[str]) -> tuple[str, ...]:
        return tuple(self._origin_app_names.get(package, package) for package in packages)

    def _format_model_candidate(self, model: SupportedPrinterModel) -> str:
        app_names = self.origin_app_names(model.origin_app_packages)
        if not app_names:
            return model.model_key
        return f"{model.model_key} ({', '.join(app_names)})"

    def require_unsupported_model(self, model_key: str) -> UnsupportedPrinterModel:
        model = self.get_unsupported_model(model_key)
        if model is None:
            raise RuntimeError(f"Unknown unsupported printer model '{model_key}'")
        return model

    def detect_model(
        self,
        device_name: str,
        address: Optional[str] = None,
    ) -> tuple[ModelMatch, ...]:
        for case_sensitive in (True, False):
            supported_matches, supported_specificity = self._best_detection_matches(
                self._supported_detection_entries,
                device_name,
                address,
                case_sensitive=case_sensitive,
            )
            unsupported_matches, unsupported_specificity = self._best_detection_matches(
                self._unsupported_detection_entries,
                device_name,
                address,
                case_sensitive=case_sensitive,
            )
            if supported_matches and unsupported_specificity is not None:
                assert supported_specificity is not None
                if unsupported_specificity > supported_specificity:
                    return tuple(
                        self._model_match(model, detection)
                        for model, detection in unsupported_matches
                    )
            if supported_matches:
                return tuple(
                    self._model_match(model, detection)
                    for model, detection in supported_matches
                )
            if unsupported_matches:
                return tuple(
                    self._model_match(model, detection)
                    for model, detection in unsupported_matches
                )
        return ()

    def detection_devices(
        self,
        device_name: str,
        address: Optional[str] = None,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> tuple[PrinterDevice, ...]:
        """Return printable device candidates for a known detected name.

        This keeps supported candidate construction in one place. It may return
        multiple devices when the same advertised name is source-app ambiguous.
        """
        return tuple(
            self.device_from_match(
                match,
                display_name=display_name or device_name,
                transport_target=transport_target,
            )
            for match in self.detect_model(device_name, address)
            if isinstance(match, SupportedModelMatch)
        )

    def _best_detection_matches(
        self,
        entries: Iterable[
            tuple[SupportedPrinterModel | UnsupportedPrinterModel, NamedModelDetection]
        ],
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool,
    ) -> tuple[
        tuple[
            tuple[SupportedPrinterModel | UnsupportedPrinterModel, NamedModelDetection],
            ...
        ],
        tuple[int, int, int, int, int] | None,
    ]:
        matches: list[
            tuple[
                SupportedPrinterModel | UnsupportedPrinterModel,
                NamedModelDetection,
                tuple[int, int, int, int, int],
            ]
        ] = []
        best_specificity: tuple[int, int, int, int, int] | None = None
        for model, detection in entries:
            specificity = self._matched_detection_specificity(
                detection,
                device_name,
                address,
                case_sensitive=case_sensitive,
            )
            if specificity is None:
                continue
            if best_specificity is None or specificity > best_specificity:
                best_specificity = specificity
                matches = [(model, detection, specificity)]
            elif specificity == best_specificity:
                matches.append((model, detection, specificity))

        deduped: list[
            tuple[SupportedPrinterModel | UnsupportedPrinterModel, NamedModelDetection]
        ] = []
        seen_model_keys: set[str] = set()
        for model, detection, _specificity in matches:
            if model.model_key in seen_model_keys:
                continue
            seen_model_keys.add(model.model_key)
            deduped.append((model, detection))
        return tuple(deduped), best_specificity

    def _model_match(
        self,
        model: SupportedPrinterModel | UnsupportedPrinterModel,
        detection: NamedModelDetection,
    ) -> SupportedModelMatch | UnsupportedModelMatch:
        if isinstance(model, SupportedPrinterModel):
            profile = self._profile_by_key.get(model.profile_key)
            if profile is None:
                raise ValueError(
                    f"Printer model {model.model_key} references unknown profile {model.profile_key}"
                )
            return SupportedModelMatch(model=model, profile=profile, detection=detection)
        assert isinstance(model, UnsupportedPrinterModel)
        return UnsupportedModelMatch(model=model, detection=detection)

    def detect_unsupported_model(
        self,
        device_name: str,
        address: Optional[str] = None,
    ) -> UnsupportedPrinterModel | None:
        matches = self.detect_model(device_name, address)
        unsupported = [
            match
            for match in matches
            if isinstance(match, UnsupportedModelMatch)
        ]
        supported = [
            match
            for match in matches
            if isinstance(match, SupportedModelMatch)
        ]
        if len(unsupported) == 1 and not supported:
            return unsupported[0].model
        return None

    def device_from_model(
        self,
        model_key: str,
        *,
        display_name: Optional[str] = None,
        transport_target: TransportTarget | None = None,
    ) -> PrinterDevice:
        model = self.require_model(model_key)
        profile = self.require_profile(model.profile_key)
        return self._build_device(
            display_name=display_name or model.names[0],
            profile=profile,
            protocol_family=self._protocol_family_for_model(model, profile),
            protocol_variant=self._protocol_packets_type_for_model(model, profile),
            image_pipeline=self._select_image_pipeline(profile, model),
            runtime_settings=self._runtime_settings_for_model(model, profile),
            model_key=model.model_key,
            origin_app_packages=model.origin_app_packages,
            transport_target=transport_target,
        )

    def serialize_printer_config(self, device: PrinterDevice) -> dict[str, Any]:
        """Serialize a device into an editable printer config object.

        The printer config keeps ``model_key`` as the catalog fallback when
        possible and writes full effective overrides so users can tune values
        in-place. Removing an override key falls back to the catalog model, or
        to the raw profile for low-level profile configs.
        """
        model_key = device.model_key if self.get_model(device.model_key) is not None else None
        return serialize_printer_config(device, model_key=model_key)

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
            require_model_device=self.device_from_model,
            require_runtime_preset=self.require_profile_runtime_preset,
        )
        resolved_transport_target = (
            printer_config_parts.transport_target
            if transport_target is _UNSET
            else transport_target
        )
        return self._build_device(
            display_name=display_name or printer_config_parts.display_name,
            profile=printer_config_parts.profile,
            protocol_family=printer_config_parts.profile.protocol_default.type,
            protocol_variant=printer_config_parts.profile.protocol_default.packets_type,
            image_pipeline=printer_config_parts.profile.default_image_pipeline,
            runtime_settings=printer_config_parts.runtime_settings,
            model_key=printer_config_parts.model_key
            or f"printer_config:{printer_config_parts.profile.profile_key}",
            origin_app_packages=printer_config_parts.origin_app_packages,
            transport_target=resolved_transport_target,
        )

    def require_profile_runtime_preset(
        self,
        profile: PrinterProfile,
        profile_runtime_preset_key: str,
    ) -> RuntimePreset:
        preset = next(
            (
                candidate
                for candidate in profile.runtime_presets
                if candidate.key == profile_runtime_preset_key
            ),
            None,
        )
        if preset is None:
            raise RuntimeError(
                f"Unknown runtime preset '{profile_runtime_preset_key}' "
                f"for profile '{profile.profile_key}'"
            )
        return preset

    def _build_device(
        self,
        *,
        display_name: str,
        profile: PrinterProfile,
        protocol_family: ProtocolFamily,
        protocol_variant: str | None,
        image_pipeline: ImagePipelineConfig,
        runtime_settings: RuntimeSettings | None,
        model_key: str,
        origin_app_packages: tuple[str, ...],
        transport_target: TransportTarget | None,
    ) -> PrinterDevice:
        self._validate_protocol_variant(protocol_family, protocol_variant)
        self._validate_profile_speed_for_family(
            profile=profile,
            protocol_family=protocol_family,
            context=f"device {display_name}",
        )
        return PrinterDevice(
            display_name=display_name,
            profile=profile,
            protocol_family=protocol_family,
            protocol_variant=protocol_variant,
            image_pipeline=image_pipeline,
            runtime_settings=runtime_settings,
            transport_target=transport_target,
            model_key=model_key,
            origin_app_packages=origin_app_packages,
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
        model: SupportedPrinterModel,
    ) -> ImagePipelineConfig:
        if model.image_pipeline is not None:
            return model.image_pipeline
        protocol_family = PrinterCatalog._protocol_family_for_model(model, profile)
        if protocol_family == profile.protocol_default.type:
            return profile.default_image_pipeline
        return get_protocol_definition(protocol_family).behavior.default_image_pipeline

    @staticmethod
    def _protocol_family_for_model(
        model: SupportedPrinterModel,
        profile: PrinterProfile,
    ) -> ProtocolFamily:
        override = model.protocol_override
        if override is not None and override.type is not None:
            return override.type
        return profile.protocol_default.type

    @staticmethod
    def _protocol_packets_type_for_model(
        model: SupportedPrinterModel,
        profile: PrinterProfile,
    ) -> str | None:
        override = model.protocol_override
        if override is not None and override.packets_type is not None:
            return override.packets_type
        if override is not None and override.type is not None and override.type != profile.protocol_default.type:
            return None
        return profile.protocol_default.packets_type

    @staticmethod
    def _runtime_settings_for_model(
        model: SupportedPrinterModel,
        profile: PrinterProfile,
    ) -> RuntimeSettings | None:
        if model.profile_runtime_preset_key is None:
            return None
        preset = next(
            (
                candidate
                for candidate in profile.runtime_presets
                if candidate.key == model.profile_runtime_preset_key
            ),
            None,
        )
        if preset is None:
            raise RuntimeError(
                f"Model {model.model_key} references unknown runtime preset "
                f"{model.profile_runtime_preset_key}"
            )
        return runtime_settings_from_parts(preset=preset)

__all__ = [
    "PROFILE_DATA_PATH",
    "MODEL_DATA_PATH",
    "UNSUPPORTED_MODEL_DATA_PATH",
    "PAPER_PRESET_DATA_PATH",
    "PrinterCatalog",
]
