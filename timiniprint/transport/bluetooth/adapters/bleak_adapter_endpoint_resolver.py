"""BLE endpoint resolver for selecting writable GATT characteristics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from .... import reporting


@dataclass(frozen=True)
class _WriteCandidate:
    service_uuid: str
    char_uuid: str
    properties: Tuple[str, ...]
    has_write: bool
    has_write_without_response: bool
    char_preferred: bool
    service_preferred: bool
    notify_preferred: bool
    char: Any


@dataclass(frozen=True)
class _WriteSelection:
    char: Any
    strategy: str
    response_preference: bool
    service_uuid: str
    char_uuid: str


class _BleWriteEndpointResolver:
    _BASE_BLUETOOTH_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"
    _PREFERRED_SERVICE_UUIDS = {
        "0000ae30-0000-1000-8000-00805f9b34fb",
        "0000ae3a-0000-1000-8000-00805f9b34fb",
        "0000ae00-0000-1000-8000-00805f9b34fb",
        "0000ae80-0000-1000-8000-00805f9b34fb",
        "0000ff00-0000-1000-8000-00805f9b34fb",
        "0000ab00-0000-1000-8000-00805f9b34fb",
        "49535343-fe7d-4ae5-8fa9-9fafd205e455",
    }
    _PREFERRED_SERVICE_SHORT = {"ae30", "ae3a", "ae00", "ae80", "ff00", "ab00"}
    _PREFERRED_WRITE_UUIDS = {
        "0000ae01-0000-1000-8000-00805f9b34fb",
        "0000ae3b-0000-1000-8000-00805f9b34fb",
        "0000ae81-0000-1000-8000-00805f9b34fb",
        "0000ff02-0000-1000-8000-00805f9b34fb",
        "0000ab01-0000-1000-8000-00805f9b34fb",
        "49535343-8841-43f4-a8d4-ecbe34729bb3",
    }
    _PREFERRED_WRITE_SHORT = {"ae01", "ae3b", "ae81", "ff02", "ab01"}
    _PREFERRED_NOTIFY_UUIDS = {
        "0000ae02-0000-1000-8000-00805f9b34fb",
        "0000ae04-0000-1000-8000-00805f9b34fb",
        "0000ae3c-0000-1000-8000-00805f9b34fb",
        "0000ab03-0000-1000-8000-00805f9b34fb",
    }
    _PREFERRED_NOTIFY_SHORT = {"ae02", "ae04", "ae3c", "ab03"}

    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter

    def resolve(self, services: Iterable[object]) -> Optional[_WriteSelection]:
        candidates = self._collect_candidates(services)
        self._log_candidates(candidates)
        return self._select_candidate(candidates)

    @classmethod
    def resolve_response_mode(
        cls,
        properties: Iterable[object],
        strategy: str,
        response_preference: Optional[bool],
    ) -> bool:
        props = cls._normalize_properties(properties)
        has_write = "write" in props
        has_write_without_response = "write-without-response" in props

        if not has_write and not has_write_without_response:
            raise RuntimeError("Characteristic does not support writing")

        if strategy == "preferred_uuid":
            # For known UUID families, prefer write-without-response when possible.
            return not has_write_without_response

        if strategy == "generic_fallback":
            # Hybrid fallback for unknown devices:
            # - only WNR => response=False
            # - only write => response=True
            # - dual support => keep resolver preference
            if has_write_without_response and not has_write:
                return False
            if has_write and not has_write_without_response:
                return True
            if response_preference is not None:
                return bool(response_preference)

        if has_write:
            return True
        return False

    @classmethod
    def _normalize_uuid(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    @classmethod
    def _uuid_short(cls, value: str) -> str:
        if (
            len(value) == 36
            and value.startswith("0000")
            and value.endswith(cls._BASE_BLUETOOTH_UUID_SUFFIX)
        ):
            return value[4:8]
        return ""

    @classmethod
    def _uuid_is_preferred(
        cls,
        value: str,
        preferred_uuids: Set[str],
        preferred_short: Set[str],
    ) -> bool:
        normalized = cls._normalize_uuid(value)
        if not normalized:
            return False
        if normalized in preferred_uuids:
            return True
        token = cls._uuid_short(normalized)
        return bool(token and token in preferred_short)

    @classmethod
    def _normalize_properties(cls, properties: Iterable[object]) -> Tuple[str, ...]:
        normalized = sorted({str(item).strip().lower() for item in properties})
        return tuple(normalized)

    @classmethod
    def _score_preferred_candidate(cls, candidate: _WriteCandidate) -> int:
        score = 0
        if candidate.char_preferred:
            score += 100
        if candidate.service_preferred:
            score += 50
        if candidate.notify_preferred:
            score += 5
        if candidate.has_write_without_response:
            score += 10
        elif candidate.has_write:
            score += 5
        return score

    @classmethod
    def _collect_candidates(cls, services: Iterable[object]) -> List[_WriteCandidate]:
        candidates: List[_WriteCandidate] = []
        for service in services:
            service_uuid = cls._normalize_uuid(getattr(service, "uuid", ""))
            for characteristic in getattr(service, "characteristics", []):
                char_uuid = cls._normalize_uuid(getattr(characteristic, "uuid", ""))
                props = cls._normalize_properties(getattr(characteristic, "properties", []))
                has_write = "write" in props
                has_write_without_response = "write-without-response" in props
                if not has_write and not has_write_without_response:
                    continue
                candidates.append(
                    _WriteCandidate(
                        service_uuid=service_uuid,
                        char_uuid=char_uuid,
                        properties=props,
                        has_write=has_write,
                        has_write_without_response=has_write_without_response,
                        char_preferred=cls._uuid_is_preferred(
                            char_uuid,
                            cls._PREFERRED_WRITE_UUIDS,
                            cls._PREFERRED_WRITE_SHORT,
                        ),
                        service_preferred=cls._uuid_is_preferred(
                            service_uuid,
                            cls._PREFERRED_SERVICE_UUIDS,
                            cls._PREFERRED_SERVICE_SHORT,
                        ),
                        notify_preferred=cls._uuid_is_preferred(
                            char_uuid,
                            cls._PREFERRED_NOTIFY_UUIDS,
                            cls._PREFERRED_NOTIFY_SHORT,
                        ),
                        char=characteristic,
                    )
                )
        return candidates

    @classmethod
    def _select_candidate(cls, candidates: Sequence[_WriteCandidate]) -> Optional[_WriteSelection]:
        preferred_candidates = [c for c in candidates if c.char_preferred or c.service_preferred]
        if preferred_candidates:
            # Deterministic preferred-UUID path for known printer families.
            selected = sorted(
                preferred_candidates,
                key=lambda c: (-cls._score_preferred_candidate(c), c.service_uuid, c.char_uuid),
            )[0]
            return _WriteSelection(
                char=selected.char,
                strategy="preferred_uuid",
                response_preference=not selected.has_write_without_response,
                service_uuid=selected.service_uuid,
                char_uuid=selected.char_uuid,
            )

        generic_wnr = sorted(
            [c for c in candidates if c.has_write_without_response],
            key=lambda c: (c.service_uuid, c.char_uuid),
        )
        if generic_wnr:
            # Unknown device fallback: prefer write-without-response first.
            selected = generic_wnr[0]
            return _WriteSelection(
                char=selected.char,
                strategy="generic_fallback",
                response_preference=False,
                service_uuid=selected.service_uuid,
                char_uuid=selected.char_uuid,
            )

        generic_write = sorted(
            [c for c in candidates if c.has_write],
            key=lambda c: (c.service_uuid, c.char_uuid),
        )
        if generic_write:
            # Last fallback: plain write characteristic.
            selected = generic_write[0]
            return _WriteSelection(
                char=selected.char,
                strategy="generic_fallback",
                response_preference=True,
                service_uuid=selected.service_uuid,
                char_uuid=selected.char_uuid,
            )

        return None

    def _log_candidates(self, candidates: Sequence[_WriteCandidate]) -> None:
        for candidate in sorted(candidates, key=lambda c: (c.service_uuid, c.char_uuid)):
            self._reporter.debug(
                short="BLE candidate",
                detail=(
                    "candidate "
                    f"service={candidate.service_uuid} char={candidate.char_uuid} "
                    f"props={list(candidate.properties)} "
                    f"char_preferred={candidate.char_preferred} "
                    f"service_preferred={candidate.service_preferred}"
                ),
            )
