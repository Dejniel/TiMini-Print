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
class PrinterRuntimeDefaults:
    key: str
    profile_key: str
    variant: str | None
    density: ModeLevelProfile
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    def select_density(self, *, is_text: bool, blackening: int) -> int:
        return self.density.select(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class RuntimeSettings:
    variant: str | None = None
    defaults: PrinterRuntimeDefaults | None = None
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    @property
    def defaults_key(self) -> str | None:
        return None if self.defaults is None else self.defaults.key

    def select_density(self, *, is_text: bool, blackening: int) -> int | None:
        if self.defaults is None:
            return None
        return self.defaults.select_density(is_text=is_text, blackening=blackening)


@dataclass(frozen=True)
class PrinterProfile:
    profile_key: str
    size: int
    paper_size: int
    print_size: int
    one_length: int
    dev_dpi: int
    can_change_mtu: bool
    has_id: bool
    use_spp: bool
    can_print_label: bool
    label_value: str
    back_paper_num: int
    default_protocol_family: ProtocolFamily
    default_image_pipeline: ImagePipelineConfig
    stream: StreamProfile
    print_defaults: PrintDefaults
    default_protocol_variant: str | None = None
    default_paper_mode: PaperMode | None = None
    post_print_feed_count: int = 2
    a4xii: bool = False
    add_mor_pix: Optional[bool] = None

    @property
    def width(self) -> int:
        return self.print_size

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
class DetectionRule:
    rule_key: str
    profile_key: str
    protocol_family: ProtocolFamily
    prefixes: tuple[str, ...] = ()
    exact_names: tuple[str, ...] = ()
    protocol_variant: str | None = None
    mac_suffixes: tuple[str, ...] = ()
    image_pipeline: ImagePipelineConfig | None = None
    runtime_variant: str | None = None
    runtime_defaults_key: str | None = None
    _folded_prefixes: tuple[str, ...] = field(init=False, repr=False)
    _folded_exact_names: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        prefixes = tuple(DetectionNormalizer.normalize_name(prefix) for prefix in self.prefixes)
        exact_names = tuple(DetectionNormalizer.normalize_name(name) for name in self.exact_names)
        if not prefixes and not exact_names:
            raise ValueError("Detection rule requires at least one prefix or exact_name")
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
            matches_name = normalized_name in self.exact_names or any(
                normalized_name.startswith(prefix) for prefix in self.prefixes
            )
        else:
            folded_name = DetectionNormalizer.fold_name(device_name)
            matches_name = folded_name in self._folded_exact_names or any(
                folded_name.startswith(prefix) for prefix in self._folded_prefixes
            )
        if not matches_name:
            return False
        if not self.mac_suffixes:
            return True
        if not address or not DetectionNormalizer.is_mac_like_address(address):
            return False
        normalized = DetectionNormalizer.normalize_mac_candidate(address)
        return any(normalized.endswith(suffix) for suffix in self.mac_suffixes)


__all__ = [
    "DetectionNormalizer",
    "DetectionRule",
    "LevelProfile",
    "ModeLevelProfile",
    "PrintDefaults",
    "PrinterProfile",
    "PrinterRuntimeDefaults",
    "RuntimeCapabilities",
    "RuntimeSettings",
    "SpeedProfile",
    "StreamProfile",
]
