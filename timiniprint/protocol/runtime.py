from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimePrintCapabilities:
    """Session-derived print capabilities that affect protocol payload construction."""

    supports_gray: bool | None = None
    gray_level_override: int | None = None
