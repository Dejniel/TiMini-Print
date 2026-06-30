from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..protocol.family import ProtocolFamily
from ..protocol.types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..raster import PixelFormat


class DetectionNormalizer:
    _whitespace_re = re.compile(r"\s+")
    _non_hex_re = re.compile(r"[^0-9A-F]")
    _mac_like_re = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

    @classmethod
    def normalize_name(cls, value: str) -> str:
        return cls._whitespace_re.sub("", value)

    @classmethod
    def fold_name(cls, value: str) -> str:
        return cls.normalize_name(value).upper()

    @classmethod
    def normalize_mac_candidate(cls, value: str) -> str:
        return cls._non_hex_re.sub("", value.upper())

    @classmethod
    def is_mac_like_address(cls, value: str) -> bool:
        return bool(cls._mac_like_re.match(value.strip()))


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
    speed: SpeedProfile | None = None
    density: ModeLevelProfile | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    d2_status: bool = False
    didian_status: bool = False


@dataclass(frozen=True)
class ProtocolDefault:
    type: ProtocolFamily
    packets_type: str | None = None


@dataclass(frozen=True)
class ProtocolOverride:
    type: ProtocolFamily | None = None
    packets_type: str | None = None


@dataclass(frozen=True)
class RuntimePreset:
    key: str
    control_algorithm: str | None
    density: ModeLevelProfile | None = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    def select_density(self, *, is_text: bool, blackening: int) -> int:
        if self.density is None:
            raise ValueError(f"Runtime preset {self.key} does not define density")
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class RuntimeSettings:
    control_algorithm: str | None = None
    preset: RuntimePreset | None = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    @property
    def preset_key(self) -> str | None:
        return None if self.preset is None else self.preset.key

    def select_density(self, *, is_text: bool, blackening: int) -> int | None:
        if self.preset is None or self.preset.density is None:
            return None
        return self.preset.select_density(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class PaperPreset:
    key: str
    label: str
    paper_width_px: int
    render_width_px: int
    paper_mode: PaperMode | None = None
    left_padding_px: int = 0
    max_height_px: int | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("paper preset requires key")
        if not self.label:
            raise ValueError(f"paper preset {self.key} requires label")
        for field_name in (
            "paper_width_px",
            "render_width_px",
            "left_padding_px",
            "max_height_px",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"paper preset {self.key} {field_name} must not be negative")
        if self.paper_width_px <= 0:
            raise ValueError(f"paper preset {self.key} paper_width_px must be greater than zero")
        if self.render_width_px <= 0:
            raise ValueError(f"paper preset {self.key} render_width_px must be greater than zero")
        if self.paper_width_px % 8 != 0:
            raise ValueError(f"paper preset {self.key} paper_width_px must be divisible by 8")
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
    runtime_presets: tuple[RuntimePreset, ...] = ()
    paper_presets: tuple[PaperPreset, ...] = ()
    default_paper_preset_key: str | None = None
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

    def paper_preset(self, key: str) -> PaperPreset | None:
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

    def paper_preset_for_mode(self, paper_mode: PaperMode | None) -> PaperPreset:
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
    def default_paper_mode(self) -> PaperMode | None:
        return self.default_paper_preset.paper_mode

    @property
    def width(self) -> int:
        return self.default_paper_preset.render_width_px

    @property
    def speed(self) -> SpeedProfile | None:
        return self.print_defaults.speed

    @property
    def energy(self) -> ModeLevelProfile:
        return self.print_defaults.energy

    @property
    def density(self) -> ModeLevelProfile | None:
        return self.print_defaults.density

    def select_speed(self, *, is_text: bool) -> int | None:
        if self.speed is None:
            return None
        return self.speed.select(is_text=is_text)

    def select_energy(self, *, is_text: bool, blackening: int) -> int:
        return self.energy.select(is_text=is_text, blackening=blackening)

    def select_density(self, *, is_text: bool, blackening: int) -> int | None:
        if self.density is None:
            return None
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class ModelDetection:
    prefixes: tuple[str, ...] = ()
    exact_names: tuple[str, ...] = ()
    mac_suffixes: tuple[str, ...] = ()
    _folded_prefixes: tuple[str, ...] = field(init=False, repr=False)
    _folded_exact_names: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        prefixes = tuple(DetectionNormalizer.normalize_name(prefix) for prefix in self.prefixes)
        exact_names = tuple(DetectionNormalizer.normalize_name(name) for name in self.exact_names)
        if not prefixes and not exact_names:
            raise ValueError("Model detection requires at least one prefix or exact_name")
        object.__setattr__(self, "prefixes", prefixes)
        object.__setattr__(self, "exact_names", exact_names)
        object.__setattr__(
            self,
            "mac_suffixes",
            tuple(str(suffix).upper() for suffix in self.mac_suffixes),
        )
        object.__setattr__(
            self,
            "_folded_prefixes",
            tuple(DetectionNormalizer.fold_name(prefix) for prefix in prefixes),
        )
        object.__setattr__(
            self,
            "_folded_exact_names",
            tuple(DetectionNormalizer.fold_name(name) for name in exact_names),
        )

    def matches(
        self,
        device_name: str,
        address: Optional[str],
        *,
        case_sensitive: bool = True,
    ) -> bool:
        normalized_name = DetectionNormalizer.normalize_name(device_name)
        if case_sensitive:
            matches_name = (
                normalized_name in self.exact_names
                or any(normalized_name.startswith(prefix) for prefix in self.prefixes)
            )
        else:
            folded_name = DetectionNormalizer.fold_name(device_name)
            matches_name = (
                folded_name in self._folded_exact_names
                or any(folded_name.startswith(prefix) for prefix in self._folded_prefixes)
            )
        if not matches_name:
            return False
        if not self.mac_suffixes:
            return True
        if not address or not DetectionNormalizer.is_mac_like_address(address):
            return False
        normalized = DetectionNormalizer.normalize_mac_candidate(address)
        return any(normalized.endswith(suffix) for suffix in self.mac_suffixes)


@dataclass(frozen=True)
class NamedModelDetection:
    name: str
    detection: ModelDetection

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Model detection requires name")

    @staticmethod
    def normalize_public_name(name: str) -> str:
        return DetectionNormalizer.normalize_name(name)

    @property
    def normalized_name(self) -> str:
        return self.normalize_public_name(self.name)


@dataclass(frozen=True, kw_only=True)
class PrinterModel:
    model_key: str
    detections: tuple[NamedModelDetection, ...]
    origin_app_packages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.model_key:
            raise ValueError("Printer model requires model_key")
        if not self.detections:
            raise ValueError(f"Printer model {self.model_key} requires detections")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(detection.name for detection in self.detections)


@dataclass(frozen=True, kw_only=True)
class SupportedPrinterModel(PrinterModel):
    profile_key: str
    protocol_override: ProtocolOverride | None = None
    image_pipeline: ImagePipelineConfig | None = None
    profile_runtime_preset_key: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.profile_key:
            raise ValueError(f"Printer model {self.model_key} requires profile_key")


@dataclass(frozen=True, kw_only=True)
class UnsupportedPrinterModel(PrinterModel):
    profile_key_prediction: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SupportedModelMatch:
    model: SupportedPrinterModel
    profile: PrinterProfile
    detection: NamedModelDetection


@dataclass(frozen=True)
class UnsupportedModelMatch:
    model: UnsupportedPrinterModel
    detection: NamedModelDetection


ModelMatch = SupportedModelMatch | UnsupportedModelMatch


__all__ = [
    "DetectionNormalizer",
    "LevelProfile",
    "PaperPreset",
    "ModelMatch",
    "ModelDetection",
    "ModeLevelProfile",
    "NamedModelDetection",
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
