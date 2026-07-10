from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class V5XJobContext:
    coverage_ratio: float = 0.0
    is_gray: bool = False


def adjust_density_payload(
    payload: bytes,
    context: V5XJobContext,
    *,
    temperature_c: int,
    head_type: str,
) -> bytes:
    if len(payload) != 1:
        return payload
    user_density = payload[0]
    if context.is_gray:
        target_density = _gray_density_target(temperature_c, user_density, head_type)
    else:
        target_density = _dot_density_target(
            temperature_c,
            user_density,
            head_type,
            context.coverage_ratio,
        )
    target_density = max(0, min(user_density, target_density))
    return bytes([target_density])


def start_delay_ms(
    context: V5XJobContext,
    *,
    density_updated: bool,
    head_type: str,
) -> int:
    # High-coverage gaoya heads need a noticeably longer settle window
    # before the print-start command becomes reliable.
    if head_type == "gaoya" and context.coverage_ratio > 0.4:
        return 200
    if density_updated:
        return 60
    return 0


def _coverage_band(coverage_ratio: float) -> int:
    if coverage_ratio <= 0.4:
        return 1
    if coverage_ratio < 0.5:
        return 2
    if coverage_ratio < 0.7:
        return 3
    return 4


def _gray_density_target(temperature_c: int, user_density: int, head_type: str) -> int:
    # Gray-mode thresholds are head-specific lookup tables rather than a
    # smooth formula.
    if head_type == "gaoya":
        thresholds = ((70, 56), (65, 65), (60, 75), (55, 80), (50, 85))
    else:
        thresholds = ((70, 56), (65, 60), (60, 65), (55, 75), (50, 80))
    for threshold, value in thresholds:
        if temperature_c >= threshold:
            return min(user_density, value)
    return user_density


def _dot_density_target(
    temperature_c: int,
    user_density: int,
    head_type: str,
    coverage_ratio: float,
) -> int:
    if temperature_c <= 60:
        return user_density
    band = _coverage_band(coverage_ratio)
    # Dot-mode fallback uses one table per head type and temperature band,
    # then picks a slot based on black coverage.
    if head_type == "gaoya":
        values = (
            (48, 15, 15, 10)
            if temperature_c < 65
            else ((36, 9, 5, 5) if temperature_c < 70 else (22, 5, 3, 3))
        )
    else:
        values = (
            (60, 50, 50, 30)
            if temperature_c <= 65
            else ((50, 40, 40, 20) if temperature_c <= 70 else (40, 30, 30, 10))
        )
    return min(user_density, values[band - 1])
