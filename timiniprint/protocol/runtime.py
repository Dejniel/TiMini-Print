from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimePrintCapabilities:
    """Immutable session findings used when choosing controls and building jobs.

    Optional booleans distinguish confirmed support/non-support from ``None``
    (unknown or not queried). Unknown values leave recipe defaults in charge.
    ``supports_gray=False`` excludes grayscale from format choices and resolves
    a grayscale request to an implemented monochrome codec. It never enables
    a codec absent from the recipe. ``gray_level_override`` is recipe-specific.
    ``supports_blackening=False`` hides that adjustment from the public
    capability query. The object itself performs no communication or discovery.
    """

    supports_gray: bool | None = None
    gray_level_override: int | None = None
    supports_blackening: bool | None = None
