from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple, Union

from ..protocol.family import ProtocolFamily
from ..protocol.types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..raster import DitherMode, PixelFormat


class WhitespaceMode(str, Enum):
    REMOVE = "remove"
    TRIM = "trim"
    PRESERVE = "preserve"


class DetectionNormalizer:
    _whitespace_re = re.compile(r"\s+")
    _non_hex_re = re.compile(r"[^0-9A-F]")
    _mac_like_re = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

    @classmethod
    def normalize_name(
        cls,
        value: str,
        whitespace_mode: WhitespaceMode = WhitespaceMode.REMOVE,
    ) -> str:
        mode = WhitespaceMode(whitespace_mode)
        if mode is WhitespaceMode.PRESERVE:
            return value
        if mode is WhitespaceMode.TRIM:
            return value.strip()
        return cls._whitespace_re.sub("", value)

    @classmethod
    def fold_name(
        cls,
        value: str,
        whitespace_mode: WhitespaceMode = WhitespaceMode.REMOVE,
    ) -> str:
        return cls.normalize_name(value, whitespace_mode).upper()

    @classmethod
    def normalize_mac_candidate(cls, value: str) -> str:
        return cls._non_hex_re.sub("", value.upper())

    @classmethod
    def is_mac_like_address(cls, value: str) -> bool:
        return bool(cls._mac_like_re.match(value.strip()))

    @staticmethod
    def public_pattern_name(value: str) -> str:
        value = value.strip()
        return value[:-1] if value.endswith(("-", "_")) else value

    @staticmethod
    def dedupe_public_names(values: Tuple[str, ...]) -> Tuple[str, ...]:
        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = value.strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return tuple(names)


@dataclass(frozen=True)
class LevelProfile:
    low: int
    middle: int
    high: int

    def select(self, blackening: int) -> int:
        level = max(1, min(5, blackening))
        if level <= 2:
            return self.low
        if level >= 4:
            return self.high
        return self.middle


@dataclass(frozen=True)
class ModeLevelProfile:
    image: LevelProfile
    text: LevelProfile

    def select(self, *, is_text: bool, blackening: int) -> int:
        target = self.text if is_text else self.image
        return target.select(blackening)


@dataclass(frozen=True)
class SpeedProfile:
    image: int
    text: int

    def select(self, *, is_text: bool) -> int:
        return self.text if is_text else self.image


@dataclass(frozen=True)
class StreamProfile:
    chunk_size: int
    delay_ms: int

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("stream.chunk_size must be greater than zero")
        if self.delay_ms < 0:
            raise ValueError("stream.delay_ms must not be negative")


@dataclass(frozen=True)
class PrintDefaults:
    energy: ModeLevelProfile
    speed: Optional[SpeedProfile] = None
    density: Optional[ModeLevelProfile] = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    d2_status: bool = False
    didian_status: bool = False


@dataclass(frozen=True)
class ProtocolDefault:
    type: ProtocolFamily
    packets_type: Optional[str] = None


@dataclass(frozen=True)
class ProtocolOverride:
    type: Optional[ProtocolFamily] = None
    packets_type: Optional[str] = None


@dataclass(frozen=True)
class RuntimePreset:
    key: str
    control_algorithm: Optional[str]
    density: Optional[ModeLevelProfile] = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    def select_density(self, *, is_text: bool, blackening: int) -> int:
        if self.density is None:
            raise ValueError(f"Runtime preset {self.key} does not define density")
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class RuntimeSettings:
    control_algorithm: Optional[str] = None
    preset: Optional[RuntimePreset] = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    @property
    def preset_key(self) -> Optional[str]:
        return None if self.preset is None else self.preset.key

    def select_density(self, *, is_text: bool, blackening: int) -> Optional[int]:
        if self.preset is None or self.preset.density is None:
            return None
        return self.preset.select_density(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class PaperPreset:
    key: str
    label: str
    paper_width_px: int
    render_width_px: int
    render_height_px: Optional[int] = None
    paper_mode: Optional[PaperMode] = None
    left_padding_px: int = 0
    top_padding_px: int = 0
    max_height_px: Optional[int] = None
    raster_height_px: Optional[int] = None
    mirror_horizontal: bool = False
    rotate_90_clockwise: bool = False
    dither_mode: Optional[DitherMode] = None
    render_height_scale: float = 1.0

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("paper preset requires key")
        if not self.label:
            raise ValueError(f"paper preset {self.key} requires label")
        for field_name in (
            "paper_width_px",
            "render_width_px",
            "render_height_px",
            "left_padding_px",
            "top_padding_px",
            "max_height_px",
            "raster_height_px",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"paper preset {self.key} {field_name} must not be negative")
        if self.paper_width_px <= 0:
            raise ValueError(f"paper preset {self.key} paper_width_px must be greater than zero")
        if self.render_width_px <= 0:
            raise ValueError(f"paper preset {self.key} render_width_px must be greater than zero")
        if self.render_height_px == 0:
            raise ValueError(f"paper preset {self.key} render_height_px must be greater than zero")
        if self.raster_height_px == 0:
            raise ValueError(f"paper preset {self.key} raster_height_px must be greater than zero")
        if self.render_height_scale <= 0:
            raise ValueError(
                f"paper preset {self.key} render_height_scale must be greater than zero"
            )
        if (
            self.render_height_px is not None
            and self.raster_height_px is not None
            and self.render_height_px + self.top_padding_px > self.raster_height_px
        ):
            raise ValueError(
                f"paper preset {self.key} render_height_px plus top_padding_px "
                "must not exceed raster_height_px"
            )
        if self.paper_width_px < self.render_width_px:
            raise ValueError(
                f"paper preset {self.key} paper_width_px must not be smaller than render_width_px"
            )
        if self.left_padding_px and self.paper_width_px != self.render_width_px + self.left_padding_px:
            raise ValueError(
                f"paper preset {self.key} paper_width_px must equal render_width_px "
                "plus left_padding_px"
            )


@dataclass(frozen=True)
class PrinterProfile:
    profile_key: str
    size: int
    one_length: int
    dev_dpi: int
    has_id: bool
    use_spp: bool
    can_print_label: bool
    label_value: str
    back_paper_num: int
    protocol_default: ProtocolDefault
    default_image_pipeline: ImagePipelineConfig
    stream: StreamProfile
    print_defaults: PrintDefaults
    runtime_presets: Tuple[RuntimePreset, ...] = ()
    paper_presets: Tuple[PaperPreset, ...] = ()
    default_paper_preset_key: Optional[str] = None
    ble_mtu_request: int = 512
    post_print_feed_count: int = 2
    a4xii: bool = False

    def __post_init__(self) -> None:
        if self.ble_mtu_request < 23:
            raise ValueError("ble_mtu_request must be at least 23")
        if not self.paper_presets:
            raise ValueError(f"profile {self.profile_key} requires at least one paper preset")
        preset_keys = [preset.key for preset in self.paper_presets]
        duplicate_keys = sorted({key for key in preset_keys if preset_keys.count(key) > 1})
        if duplicate_keys:
            raise ValueError(
                f"profile {self.profile_key} repeats paper preset keys: "
                + ", ".join(duplicate_keys)
            )
        if self.default_paper_preset_key is not None and self.default_paper_preset_key not in preset_keys:
            raise ValueError(
                f"profile {self.profile_key} default_paper_preset_key "
                f"{self.default_paper_preset_key} is not defined"
            )

    def paper_preset(self, key: str) -> Optional[PaperPreset]:
        return next((preset for preset in self.paper_presets if preset.key == key), None)

    @property
    def default_paper_preset(self) -> PaperPreset:
        if self.default_paper_preset_key is None:
            return self.paper_presets[0]
        preset = self.paper_preset(self.default_paper_preset_key)
        if preset is None:
            raise ValueError(
                f"profile {self.profile_key} default_paper_preset_key "
                f"{self.default_paper_preset_key} is not defined"
            )
        return preset

    def paper_preset_for_mode(self, paper_mode: Optional[PaperMode]) -> PaperPreset:
        if paper_mode is None:
            return self.default_paper_preset
        preset = next(
            (preset for preset in self.paper_presets if preset.paper_mode == paper_mode),
            None,
        )
        if preset is None:
            available = ", ".join(
                preset.paper_mode.value
                for preset in self.paper_presets
                if preset.paper_mode is not None
            )
            suffix = f"; available: {available}" if available else ""
            raise ValueError(
                f"profile {self.profile_key} does not define paper mode "
                f"{paper_mode.value}{suffix}"
            )
        return preset

    @property
    def default_paper_mode(self) -> Optional[PaperMode]:
        return self.default_paper_preset.paper_mode

    @property
    def speed(self) -> Optional[SpeedProfile]:
        return self.print_defaults.speed

    @property
    def energy(self) -> ModeLevelProfile:
        return self.print_defaults.energy

    @property
    def density(self) -> Optional[ModeLevelProfile]:
        return self.print_defaults.density

    def select_speed(self, *, is_text: bool) -> Optional[int]:
        if self.speed is None:
            return None
        return self.speed.select(is_text=is_text)

    def select_energy(self, *, is_text: bool, blackening: int) -> int:
        return self.energy.select(is_text=is_text, blackening=blackening)

    def select_density(self, *, is_text: bool, blackening: int) -> Optional[int]:
        if self.density is None:
            return None
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class ModelDetection:
    prefixes: Tuple[str, ...] = ()
    exact_names: Tuple[str, ...] = ()
    substrings: Tuple[str, ...] = ()
    mac_suffixes: Tuple[str, ...] = ()
    marketing_names: Tuple[str, ...] = ()
    all_of: bool = False

    def __post_init__(self) -> None:
        prefixes = tuple(self.prefixes)
        exact_names = tuple(self.exact_names)
        substrings = tuple(self.substrings)
        marketing_names = tuple(value.strip() for value in self.marketing_names)
        if any(not value.strip() for value in (*prefixes, *exact_names, *substrings)):
            raise ValueError("Model detection triggers must not be blank")
        if any(not value for value in marketing_names):
            raise ValueError("Model detection marketing_names must not contain blanks")
        if not prefixes and not exact_names and not substrings:
            raise ValueError(
                "Model detection requires at least one prefix, exact_name, or substring"
            )
        populated_name_groups = sum(
            bool(values)
            for values in (prefixes, exact_names, substrings)
        )
        if self.all_of and populated_name_groups < 2:
            raise ValueError(
                "Model detection all_of requires at least two name trigger groups"
            )
        object.__setattr__(self, "prefixes", prefixes)
        object.__setattr__(self, "exact_names", exact_names)
        object.__setattr__(self, "substrings", substrings)
        object.__setattr__(self, "marketing_names", marketing_names)
        object.__setattr__(
            self,
            "mac_suffixes",
            tuple(str(suffix).strip().upper() for suffix in self.mac_suffixes),
        )
        if not self.names:
            raise ValueError("Model detection requires at least one public name")

    @property
    def names(self) -> Tuple[str, ...]:
        public_substrings = () if self.all_of else self.substrings
        return DetectionNormalizer.dedupe_public_names(
            (
                *self.marketing_names,
                *self.exact_names,
                *(
                    DetectionNormalizer.public_pattern_name(value)
                    for value in self.prefixes
                ),
                *(
                    DetectionNormalizer.public_pattern_name(value)
                    for value in public_substrings
                ),
            )
        )

    def matches(
        self,
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool = True,
        whitespace_mode: WhitespaceMode = WhitespaceMode.REMOVE,
    ) -> bool:
        return self.matched_specificity(
            device_name,
            address,
            case_sensitive=case_sensitive,
            whitespace_mode=whitespace_mode,
        ) is not None

    def matched_specificity(
        self,
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool = True,
        whitespace_mode: WhitespaceMode = WhitespaceMode.REMOVE,
    ) -> Tuple[int, int, int, int, int] | None:
        has_mac_suffix = bool(self.mac_suffixes)
        if has_mac_suffix:
            if not address or not DetectionNormalizer.is_mac_like_address(address):
                return None
            normalized_address = DetectionNormalizer.normalize_mac_candidate(address)
            if not any(normalized_address.endswith(suffix) for suffix in self.mac_suffixes):
                return None

        normalized_name = DetectionNormalizer.normalize_name(
            device_name,
            whitespace_mode,
        )
        normalized_exact_names = tuple(
            DetectionNormalizer.normalize_name(value, whitespace_mode)
            for value in self.exact_names
        )
        normalized_prefixes = tuple(
            DetectionNormalizer.normalize_name(value, whitespace_mode)
            for value in self.prefixes
        )
        normalized_substrings = tuple(
            DetectionNormalizer.normalize_name(value, whitespace_mode)
            for value in self.substrings
        )
        if case_sensitive:
            target_name = normalized_name
            exact_names = zip(normalized_exact_names, normalized_exact_names)
            prefixes = zip(normalized_prefixes, normalized_prefixes)
            substrings = zip(normalized_substrings, normalized_substrings)
        else:
            target_name = normalized_name.upper()
            exact_names = zip(
                normalized_exact_names,
                (value.upper() for value in normalized_exact_names),
            )
            prefixes = zip(
                normalized_prefixes,
                (value.upper() for value in normalized_prefixes),
            )
            substrings = zip(
                normalized_substrings,
                (value.upper() for value in normalized_substrings),
            )

        matched_exact_names: list[Tuple[int, int, int, int, int]] = []
        for trigger, candidate in exact_names:
            if target_name == candidate:
                matched_exact_names.append(
                    self._trigger_specificity(
                        trigger,
                        match_rank=2,
                        has_mac_suffix=has_mac_suffix,
                    )
                )
        matched_prefixes: list[Tuple[int, int, int, int, int]] = []
        for trigger, candidate in prefixes:
            if target_name.startswith(candidate):
                matched_prefixes.append(
                    self._trigger_specificity(
                        trigger,
                        match_rank=1,
                        has_mac_suffix=has_mac_suffix,
                    )
                )
        matched_substrings: list[Tuple[int, int, int, int, int]] = []
        for trigger, candidate in substrings:
            if candidate in target_name:
                matched_substrings.append(
                    self._trigger_specificity(
                        trigger,
                        match_rank=0,
                        has_mac_suffix=has_mac_suffix,
                    )
                )
        matched_groups = (
            (self.exact_names, matched_exact_names),
            (self.prefixes, matched_prefixes),
            (self.substrings, matched_substrings),
        )
        if self.all_of:
            if any(configured and not matched for configured, matched in matched_groups):
                return None
            best_by_group = [
                max(matched)
                for configured, matched in matched_groups
                if configured
            ]
            return (
                sum(value[0] for value in best_by_group),
                int(has_mac_suffix),
                sum(value[2] for value in best_by_group),
                sum(value[3] for value in best_by_group),
                sum(value[4] for value in best_by_group),
            )

        return max(
            (*matched_exact_names, *matched_prefixes, *matched_substrings),
            default=None,
        )

    @staticmethod
    def _trigger_specificity(
        trigger: str,
        *,
        match_rank: int,
        has_mac_suffix: bool,
    ) -> Tuple[int, int, int, int, int]:
        normalized_trigger = DetectionNormalizer.normalize_name(trigger)
        trigger_length = (
            len(normalized_trigger[:-1])
            if normalized_trigger.endswith(("-", "_"))
            else len(normalized_trigger)
        )
        return (
            trigger_length,
            int(has_mac_suffix),
            match_rank,
            len(normalized_trigger),
            sum(1 for char in normalized_trigger if char.isupper()),
        )

@dataclass(frozen=True)
class PrinterModel:
    model_key: str
    detections: Tuple[ModelDetection, ...]
    whitespace_mode: WhitespaceMode = WhitespaceMode.REMOVE
    marketing_names: Tuple[str, ...] = ()
    origin_app_packages: Tuple[str, ...] = ()
    detection_ambiguity_group: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.model_key:
            raise ValueError("Printer model requires model_key")
        object.__setattr__(
            self,
            "whitespace_mode",
            WhitespaceMode(self.whitespace_mode),
        )
        marketing_names = tuple(value.strip() for value in self.marketing_names)
        if any(not value for value in marketing_names):
            raise ValueError(
                f"Printer model {self.model_key} marketing_names must not contain blanks"
            )
        object.__setattr__(self, "marketing_names", marketing_names)
        if not self.detections:
            raise ValueError(f"Printer model {self.model_key} requires detections")
        if self.detection_ambiguity_group is not None:
            group = self.detection_ambiguity_group.strip()
            if not group:
                raise ValueError(
                    f"Printer model {self.model_key} has blank detection_ambiguity_group"
                )
            object.__setattr__(self, "detection_ambiguity_group", group)

    @property
    def names(self) -> Tuple[str, ...]:
        return DetectionNormalizer.dedupe_public_names(
            (
                *self.marketing_names,
                *(name for detection in self.detections for name in detection.names),
            )
        )


@dataclass(frozen=True)
class SupportedPrinterModel(PrinterModel):
    profile_key: str = ""
    protocol_override: Optional[ProtocolOverride] = None
    image_pipeline: Optional[ImagePipelineConfig] = None
    profile_runtime_preset_key: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.profile_key:
            raise ValueError(f"Printer model {self.model_key} requires profile_key")


@dataclass(frozen=True)
class UnsupportedPrinterModel(PrinterModel):
    profile_key_prediction: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class SupportedModelMatch:
    model: SupportedPrinterModel
    profile: PrinterProfile
    detection: ModelDetection


@dataclass(frozen=True)
class UnsupportedModelMatch:
    model: UnsupportedPrinterModel
    detection: ModelDetection


ModelMatch = Union[SupportedModelMatch, UnsupportedModelMatch]


__all__ = [
    "DetectionNormalizer",
    "WhitespaceMode",
    "LevelProfile",
    "PaperPreset",
    "ModelMatch",
    "ModelDetection",
    "ModeLevelProfile",
    "PrintDefaults",
    "PrinterModel",
    "PrinterProfile",
    "ProtocolDefault",
    "ProtocolOverride",
    "RuntimePreset",
    "RuntimeCapabilities",
    "RuntimeSettings",
    "SpeedProfile",
    "StreamProfile",
    "SupportedPrinterModel",
    "SupportedModelMatch",
    "UnsupportedPrinterModel",
    "UnsupportedModelMatch",
]
