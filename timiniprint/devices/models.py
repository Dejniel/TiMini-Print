from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, Optional, List, Tuple

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "printer_models.json"
ALIAS_PATH = DATA_PATH.with_name("printer_model_aliases.json")

class PrinterModelAliasNormalizer:
    _whitespace_re = re.compile(r"\s+")
    _non_hex_re = re.compile(r"[^0-9A-F]")

    @classmethod
    def normalize_alias_name(cls, value: str) -> str:
        return cls._whitespace_re.sub("", value).upper()

    @classmethod
    def normalize_mac_candidate(cls, value: str) -> str:
        return cls._non_hex_re.sub("", value.upper())


class PrinterModelMatchSource(Enum):
    HEAD_NAME = "head_name"
    MODEL_NO = "model_no"
    ALIAS = "alias"


class PrinterModelAliasKind(Enum):
    HEAD_NAME = "head_name"
    MAC = "mac"


@dataclass(frozen=True)
class PrinterModel:
    model_no: str
    model: int
    size: int
    paper_size: int
    print_size: int
    one_length: int
    head_name: str
    can_change_mtu: bool
    dev_dpi: int
    img_print_speed: int
    text_print_speed: int
    img_mtu: int
    new_compress: bool
    paper_num: int
    interval_ms: int
    thin_energy: int
    moderation_energy: int
    deepen_energy: int
    text_energy: int
    has_id: bool
    use_spp: bool
    new_format: bool
    can_print_label: bool
    label_value: str
    back_paper_num: int
    a4xii: bool = False
    add_mor_pix: Optional[bool] = None

    @property
    def width(self) -> int:
        return self.print_size


@dataclass(frozen=True)
class PrinterModelMatch:
    model: PrinterModel
    source: PrinterModelMatchSource
    alias_kind: Optional[PrinterModelAliasKind] = None

    @property
    def used_alias(self) -> bool:
        return self.source is PrinterModelMatchSource.ALIAS


@dataclass(frozen=True)
class PrinterModelAliasMatch:
    target_head_name: str
    kind: PrinterModelAliasKind


@dataclass(frozen=True)
class PrinterModelHeadAlias:
    prefixes: List[str]
    map_model_head_name: str
    _normalized_prefixes: Tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        normalized = tuple(
            PrinterModelAliasNormalizer.normalize_alias_name(prefix) for prefix in self.prefixes
        )
        object.__setattr__(self, "_normalized_prefixes", normalized)

    def match_length(self, normalized_name: str) -> int:
        longest = 0
        for prefix in self._normalized_prefixes:
            if normalized_name.startswith(prefix):
                longest = max(longest, len(prefix))
        return longest


@dataclass(frozen=True)
class PrinterModelMacAlias:
    suffixes: List[str]
    map_model_head_name: str
    _normalized_suffixes: Tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        normalized = tuple(suffix.upper() for suffix in self.suffixes)
        object.__setattr__(self, "_normalized_suffixes", normalized)

    def matches(self, address: Optional[str]) -> bool:
        if not address:
            return False
        candidates = [address.strip().upper()]
        normalized = PrinterModelAliasNormalizer.normalize_mac_candidate(address)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        for suffix in self._normalized_suffixes:
            for candidate in candidates:
                if candidate.endswith(suffix):
                    return True
        return False


class PrinterModelAliasRegistry:
    def __init__(
        self,
        head_aliases: Iterable[PrinterModelHeadAlias],
        mac_aliases: Iterable[PrinterModelMacAlias],
    ) -> None:
        self._head_aliases = list(head_aliases)
        self._mac_aliases = list(mac_aliases)

    @classmethod
    def load(cls, path: Path) -> "PrinterModelAliasRegistry":
        if not path.exists():
            return cls([], [])
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Alias file must contain a JSON list")
        head_aliases, mac_aliases = cls._parse(raw)
        return cls(head_aliases, mac_aliases)

    @staticmethod
    def _parse(
        raw: List[object],
    ) -> Tuple[List[PrinterModelHeadAlias], List[PrinterModelMacAlias]]:
        head_aliases: List[PrinterModelHeadAlias] = []
        mac_aliases: List[PrinterModelMacAlias] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("Alias entries must be JSON objects")
            if "head_name" in entry:
                head_name = entry.get("head_name") or {}
                if not isinstance(head_name, dict):
                    raise ValueError("Alias head_name must be a JSON object")
                prefixes = head_name.get("prefixes")
                if prefixes is None:
                    prefix = head_name.get("prefix")
                    if not prefix:
                        raise ValueError("Alias head_name missing prefix")
                    prefixes = [prefix]
                map_model_head_name = entry.get("map_model_head_name") or head_name.get(
                    "map_model_head_name"
                )
                if not map_model_head_name:
                    raise ValueError("Alias entry missing map_model_head_name")
                head_aliases.append(
                    PrinterModelHeadAlias(
                        prefixes=list(prefixes),
                        map_model_head_name=map_model_head_name,
                    )
                )
                continue
            if "mac" in entry:
                mac_entry = entry.get("mac") or {}
                if not isinstance(mac_entry, dict):
                    raise ValueError("Alias mac must be a JSON object")
                suffixes = mac_entry.get("suffixes")
                if suffixes is None:
                    suffix = mac_entry.get("suffix")
                    if not suffix:
                        raise ValueError("Alias mac missing suffix")
                    suffixes = [suffix]
                map_model_head_name = entry.get("map_model_head_name") or mac_entry.get(
                    "map_model_head_name"
                )
                if not map_model_head_name:
                    raise ValueError("Alias entry missing map_model_head_name")
                mac_aliases.append(
                    PrinterModelMacAlias(
                        suffixes=list(suffixes),
                        map_model_head_name=map_model_head_name,
                    )
                )
                continue
            raise ValueError("Alias entry must include head_name or mac")
        return head_aliases, mac_aliases

    def resolve(self, name: str, address: Optional[str]) -> Optional[PrinterModelAliasMatch]:
        if not name or not self._head_aliases:
            return None
        normalized_name = PrinterModelAliasNormalizer.normalize_alias_name(name)
        match = None
        match_prefix_len = 0
        for alias in self._head_aliases:
            prefix_len = alias.match_length(normalized_name)
            if prefix_len > match_prefix_len:
                match = alias
                match_prefix_len = prefix_len
        if not match:
            return None
        target = match.map_model_head_name
        match_kind = PrinterModelAliasKind.HEAD_NAME
        for mac_alias in self._mac_aliases:
            if mac_alias.matches(address):
                target = mac_alias.map_model_head_name
                match_kind = PrinterModelAliasKind.MAC
                break
        return PrinterModelAliasMatch(target_head_name=target, kind=match_kind)


class PrinterModelRegistry:
    _cache: Dict[Tuple[Path, Path], "PrinterModelRegistry"] = {}

    def __init__(self, models: Iterable[PrinterModel], alias_registry: PrinterModelAliasRegistry) -> None:
        self._models = list(models)
        self._aliases = alias_registry

    @classmethod
    def load(cls, path: Path = DATA_PATH) -> "PrinterModelRegistry":
        alias_path = path.with_name(ALIAS_PATH.name)
        key = (path.resolve(), alias_path.resolve())
        cached = cls._cache.get(key)
        if cached:
            return cached
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = [PrinterModel(**item) for item in raw]
        alias_registry = PrinterModelAliasRegistry.load(alias_path)
        registry = cls(models, alias_registry)
        cls._cache[key] = registry
        return registry

    @property
    def models(self) -> List[PrinterModel]:
        return list(self._models)

    def get(self, model_no: str) -> Optional[PrinterModel]:
        for model in self._models:
            if model.model_no == model_no:
                return model
        return None

    def get_by_head_name(self, head_name: str) -> Optional[PrinterModel]:
        if not head_name:
            return None
        target = head_name.lower()
        for model in self._models:
            if model.head_name and model.head_name.lower() == target:
                return model
        for model in self._models:
            if model.model_no.lower() == target:
                return model
        return None

    def detect_from_device_name(self, name: str, address: Optional[str] = None) -> Optional[PrinterModel]:
        match = self.detect_with_origin(name, address)
        if not match:
            return None
        return match.model

    def detect_with_origin(self, name: str, address: Optional[str] = None) -> Optional[PrinterModelMatch]:
        if not name:
            return None
        name_lower = name.lower()
        match = None
        for model in self._models:
            if model.head_name and name_lower.startswith(model.head_name.lower()):
                if match is None or len(model.head_name) > len(match.head_name):
                    match = model
        if match:
            return PrinterModelMatch(model=match, source=PrinterModelMatchSource.HEAD_NAME)
        for model in self._models:
            if name_lower.startswith(model.model_no.lower()):
                if match is None or len(model.model_no) > len(match.model_no):
                    match = model
        if match:
            return PrinterModelMatch(model=match, source=PrinterModelMatchSource.MODEL_NO)
        return self._detect_from_alias(name, address)

    def _detect_from_alias(self, name: str, address: Optional[str]) -> Optional[PrinterModelMatch]:
        alias_match = self._aliases.resolve(name, address)
        if not alias_match:
            return None
        model = self.get_by_head_name(alias_match.target_head_name)
        if not model:
            return None
        return PrinterModelMatch(
            model=model,
            source=PrinterModelMatchSource.ALIAS,
            alias_kind=alias_match.kind,
        )
