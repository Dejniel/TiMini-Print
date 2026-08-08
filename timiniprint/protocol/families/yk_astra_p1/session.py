from __future__ import annotations

from dataclasses import dataclass

from ..yk_common import YkFrame, iter_yk_frames, pack_yk_frame

STATUS_QUERY_COMMAND = 0x10
PAPER_QUERY_COMMAND = 0x72
PAPER_REPLY_COMMAND = 0x73
STATUS_REPLY_COMMANDS = (0x80, 0x81, 0xFF)
COMPLETION_STATUS_COMMANDS = (0x81, 0xFF)


@dataclass(frozen=True)
class AstraP1Status:
    command: int
    voltage_raw: int
    status_bits: int
    density: int
    auto_off_time: int
    speed: int
    battery_percent: int | None

    @property
    def is_printing(self) -> bool:
        return bool(self.status_bits & (1 << 3))

    @property
    def cover_open(self) -> bool:
        return bool(self.status_bits & (1 << 8))

    @property
    def low_battery(self) -> bool:
        return bool(self.status_bits & (1 << 9))

    @property
    def overheating(self) -> bool:
        return bool(self.status_bits & (1 << 10))

    @property
    def paper_out(self) -> bool:
        return bool(self.status_bits & (1 << 11))

    @property
    def valid_paper(self) -> bool:
        return bool(self.status_bits & (1 << 13))

    @property
    def paper_loaded(self) -> bool:
        if self.command == 0xFF:
            return bool((self.voltage_raw & 0xFFFF) & (1 << 2))
        return bool(self.status_bits & (1 << 15))

    @property
    def completion_status(self) -> bool:
        return self.command in COMPLETION_STATUS_COMMANDS

    @property
    def errors(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.cover_open:
            values.append("cover open")
        if self.low_battery:
            values.append("low battery")
        if self.overheating:
            values.append("overheating")
        if self.paper_out:
            values.append("out of paper")
        return tuple(values)


@dataclass(frozen=True)
class AstraP1PaperDetails:
    paper_type: int
    width: int
    length: int
    color: int


def pack_status_query(*, sequence: int = 0) -> bytes:
    return pack_yk_frame(STATUS_QUERY_COMMAND, sequence=sequence)


def pack_paper_query(*, sequence: int = 0) -> bytes:
    return pack_yk_frame(PAPER_QUERY_COMMAND, b"\x01", sequence=sequence)


def status_from_frame(frame: YkFrame) -> AstraP1Status | None:
    payload = frame.payload
    if frame.command in (0x80, 0x81) and len(payload) != 8:
        return None
    if frame.command == 0xFF and len(payload) not in (7, 8, 12):
        return None
    if frame.command not in STATUS_REPLY_COMMANDS:
        return None
    return AstraP1Status(
        command=frame.command,
        voltage_raw=int.from_bytes(payload[0:2], "little", signed=True),
        status_bits=int.from_bytes(payload[2:4], "little"),
        density=payload[4],
        auto_off_time=payload[5],
        speed=payload[6],
        battery_percent=payload[7] if len(payload) >= 8 else None,
    )


def paper_details_from_frame(frame: YkFrame) -> AstraP1PaperDetails | None:
    if frame.command != PAPER_REPLY_COMMAND or len(frame.payload) != 4:
        return None
    return AstraP1PaperDetails(*frame.payload[:4])


def first_status(data: bytes | None) -> AstraP1Status | None:
    if not data:
        return None
    return next(
        (
            status
            for frame in iter_yk_frames(data)
            if (status := status_from_frame(frame)) is not None
        ),
        None,
    )


def first_paper_details(data: bytes | None) -> AstraP1PaperDetails | None:
    if not data:
        return None
    return next(
        (
            details
            for frame in iter_yk_frames(data)
            if (details := paper_details_from_frame(frame)) is not None
        ),
        None,
    )


__all__ = [
    "AstraP1PaperDetails",
    "AstraP1Status",
    "first_paper_details",
    "first_status",
    "pack_paper_query",
    "pack_status_query",
    "paper_details_from_frame",
    "status_from_frame",
]
