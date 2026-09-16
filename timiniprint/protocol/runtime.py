from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimePrintCapabilities:
    """Immutable session findings used when choosing controls and building jobs.

    Optional booleans distinguish confirmed support/non-support from ``None``
    (unknown or not queried). Unknown values leave recipe defaults in charge.
    ``supports_gray`` and ``gray_level_override`` are consumed by recipes that
    implement those negotiations; they are not universal codec switches.
    ``supports_blackening=False`` hides that adjustment from the public
    capability query. The object itself performs no communication or discovery.
    """

    supports_gray: bool | None = None
    gray_level_override: int | None = None
    supports_blackening: bool | None = None
