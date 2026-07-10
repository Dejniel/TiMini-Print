from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DensityLevels:
    low: int
    middle: int
    high: int


@dataclass(frozen=True)
class V5GContinuousPlan:
    begin_density_value: int
    unchanged_packet_count: int
    minimum_density_value: int
    update_first_packet: bool
    clamp_low_70: bool = False


def clamp_density_value(value: int) -> int:
    return max(0, min(0xFFFF, int(value)))


def mx06_single_density_value(current_value: int, last_density_value: int) -> int:
    # MX06-style single jobs clamp hard to avoid immediate thermal spikes after
    # a recent dense print.
    value = current_value
    if last_density_value > 0:
        value = min(last_density_value, current_value)
    if value > 150:
        value = 150
    value -= 20
    if value < 70:
        value = 70
    return clamp_density_value(value)


def mx10_single_density_value(temperature_c: int, levels: DensityLevels, current_value: int) -> int:
    # These temperature breakpoints mirror the step-down helper used by MX10-
    # style devices once the head moves out of the safe range.
    value = current_value
    if temperature_c < 55:
        if value >= levels.middle:
            value = levels.middle - 20
    elif temperature_c < 60:
        if value >= levels.low:
            value = levels.low - 10
    elif temperature_c < 65:
        if value >= levels.low:
            value = levels.low - 30
    elif temperature_c < 70:
        if value >= levels.low:
            value = levels.low - 55
    elif temperature_c < 75:
        value = 80
    return clamp_density_value(value)


def pd01_single_density_value(temperature_c: int, levels: DensityLevels, current_value: int) -> int:
    # PD01 follows a slightly shallower fallback curve than MX10 at the same
    # temperatures.
    value = current_value
    if temperature_c < 55:
        if value >= levels.middle:
            value = levels.middle - 10
    elif temperature_c < 60:
        if value >= levels.middle:
            value = levels.middle - 20
    elif temperature_c < 70:
        if value >= levels.low:
            value = levels.low
    else:
        value = 90 if temperature_c < 75 else 80
    return clamp_density_value(value)


def mx10_continuous_plan(
    temperature_c: int,
    levels: DensityLevels,
    current_value: int,
) -> V5GContinuousPlan:
    # Continuous jobs keep the first few density packets steady, then decay
    # toward a floor that depends on the current head temperature.
    begin_value = min(levels.middle, current_value)
    unchanged_packets = 4
    minimum_value = 95
    update_first = False
    if temperature_c <= 50:
        if begin_value >= levels.middle - 20:
            begin_value = levels.middle - 20
            unchanged_packets = 1
            minimum_value = 90
            update_first = True
    elif temperature_c <= 55:
        if begin_value >= levels.low - 5:
            begin_value = levels.low - 5
            unchanged_packets = 1
            minimum_value = 85
            update_first = True
    elif temperature_c <= 60:
        if begin_value >= levels.low - 20:
            begin_value = levels.low - 20
            unchanged_packets = 1
            minimum_value = 75
            update_first = True
    elif temperature_c <= 65:
        if begin_value >= levels.low - 50:
            begin_value = levels.low - 50
            unchanged_packets = 1
            minimum_value = 70
            update_first = True
    else:
        begin_value = 80
        unchanged_packets = 1
        minimum_value = 70
        update_first = True
    return V5GContinuousPlan(
        begin_density_value=clamp_density_value(begin_value),
        unchanged_packet_count=max(0, unchanged_packets),
        minimum_density_value=clamp_density_value(minimum_value),
        update_first_packet=update_first,
    )


def pd01_continuous_plan(
    temperature_c: int,
    levels: DensityLevels,
    current_value: int,
    *,
    shallow: bool = False,
) -> V5GContinuousPlan:
    # PD01 has two related curves; the shallow branch is kept here for parity
    # with the observed firmware helper even though normal jobs use the default
    # branch today.
    begin_value = min(levels.middle, current_value)
    unchanged_packets = 4
    minimum_value = 95
    update_first = False
    if shallow:
        if temperature_c <= 50:
            if begin_value >= levels.middle:
                begin_value = levels.middle
                unchanged_packets = 1
                minimum_value = 90
                update_first = True
        elif temperature_c <= 55:
            if begin_value >= levels.middle - 10:
                begin_value = levels.middle - 10
                unchanged_packets = 1
                minimum_value = 85
                update_first = True
        elif temperature_c <= 60:
            if begin_value >= levels.low:
                begin_value = levels.low
                unchanged_packets = 1
                minimum_value = 75
                update_first = True
        elif temperature_c <= 65:
            if begin_value >= levels.low:
                begin_value = levels.low
                unchanged_packets = 1
                minimum_value = 70
                update_first = True
        else:
            begin_value = 90
            unchanged_packets = 1
            minimum_value = 70
            update_first = True
    else:
        if temperature_c <= 50:
            if begin_value >= levels.middle - 10:
                begin_value = levels.middle - 10
                unchanged_packets = 1
                minimum_value = 90
                update_first = True
        elif temperature_c <= 55:
            if begin_value >= levels.low - 5:
                begin_value = levels.low - 5
                unchanged_packets = 1
                minimum_value = 85
                update_first = True
        elif temperature_c <= 60:
            if begin_value >= levels.low - 20:
                begin_value = levels.low - 20
                unchanged_packets = 1
                minimum_value = 75
                update_first = True
        elif temperature_c <= 65:
            if begin_value >= levels.low - 50:
                begin_value = levels.low - 50
                unchanged_packets = 1
                minimum_value = 70
                update_first = True
        else:
            begin_value = 80
            unchanged_packets = 1
            minimum_value = 70
            update_first = True
    return V5GContinuousPlan(
        begin_density_value=clamp_density_value(begin_value),
        unchanged_packet_count=max(0, unchanged_packets),
        minimum_density_value=clamp_density_value(minimum_value),
        update_first_packet=update_first,
    )


def mx06_continuous_plan(
    levels: DensityLevels,
    current_value: int,
    *,
    last_record_density: int | None,
    recent_completion: bool,
) -> V5GContinuousPlan:
    # MX06 reuses the last completed density as a restart hint; recent jobs use
    # a harder clamp to avoid overheating on back-to-back prints.
    begin_value = min(levels.middle, current_value)
    if last_record_density is not None:
        if recent_completion:
            begin_value = min(last_record_density, begin_value) - 10
            clamp_low_70 = True
        else:
            begin_value = min(110, begin_value)
            clamp_low_70 = False
    else:
        begin_value = min(110, begin_value)
        clamp_low_70 = False
    return V5GContinuousPlan(
        begin_density_value=clamp_density_value(begin_value),
        unchanged_packet_count=4,
        minimum_density_value=95,
        update_first_packet=True,
        clamp_low_70=clamp_low_70,
    )


def mx10_continuous_series(start_value: int, count: int, *, minimum_value: int) -> list[int]:
    values: list[int] = []
    step = 15 if start_value > 135 else 10
    for index in range(1, max(0, count) + 1):
        current = start_value - (step * index)
        if current < minimum_value:
            current = minimum_value
        values.append(clamp_density_value(current))
    return values


def v5g_continuous_series(start_value: int, count: int, *, clamp_low_70: bool = False) -> list[int]:
    values: list[int] = []
    step = 5 if clamp_low_70 else 10
    for index in range(1, max(0, count) + 1):
        current = start_value - (step * index)
        if clamp_low_70 and current < 70:
            current = 70
        values.append(clamp_density_value(current))
    return values


def pd01_continuous_series(start_value: int, count: int, *, shallow: bool = False) -> list[int]:
    values: list[int] = []
    current = start_value
    for _ in range(max(0, count)):
        if shallow:
            current -= 5
            if current < 95:
                current = 95
        else:
            if current > 90:
                step = 15
            elif current == 90:
                step = 10
            else:
                step = 5
            current -= step
            if current < 55:
                current = 55
        values.append(clamp_density_value(current))
    return values
