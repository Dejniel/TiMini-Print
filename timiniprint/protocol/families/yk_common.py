from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

FRAME_START = 0x64
FRAME_END = 0x9B
SEQUENCE_MODULUS = 64
_INTEGRITY_SEED = 0x12345678


def pack_yk_frame(
    command: int,
    payload: bytes = b"",
    *,
    sequence: int = 0,
    calculated_integrity: bool = False,
) -> bytes:
    body = bytes(payload)
    if len(body) > 0xFFFF:
        raise ValueError("YK frame payload must fit in uint16")
    prefix = (
        bytes((_byte(command, "command"), sequence % SEQUENCE_MODULUS))
        + len(body).to_bytes(2, "little")
        + body
    )
    framed_prefix = bytes((FRAME_START,)) + prefix
    integrity = (
        (_INTEGRITY_SEED + sum(framed_prefix) + FRAME_END) & 0xFFFFFFFF
        if calculated_integrity
        else 0
    )
    return framed_prefix + integrity.to_bytes(4, "little") + bytes((FRAME_END,))


def resequence_yk_frame(packet: bytes, *, sequence: int) -> bytes:
    frame = next(iter_yk_frames(packet), None)
    if frame is None or frame.packet_length != len(packet):
        raise ValueError("Invalid YK frame")
    return pack_yk_frame(
        frame.command,
        frame.payload,
        sequence=sequence,
        calculated_integrity=frame.integrity != 0,
    )


@dataclass(frozen=True)
class YkFrame:
    command: int
    sequence: int
    payload: bytes
    integrity: int
    packet_length: int


class YkFrameStreamDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> tuple[YkFrame, ...]:
        self._buffer.extend(data)
        frames: list[YkFrame] = []
        while self._buffer:
            marker = self._buffer.find(bytes((FRAME_START,)))
            if marker < 0:
                self._buffer.clear()
                break
            if marker:
                del self._buffer[:marker]
            if len(self._buffer) < 5:
                break
            payload_length = int.from_bytes(self._buffer[3:5], "little")
            packet_length = payload_length + 10
            if len(self._buffer) < packet_length:
                break
            packet = bytes(self._buffer[:packet_length])
            if packet[-1] != FRAME_END or not _integrity_valid(packet):
                del self._buffer[0]
                continue
            frames.append(
                YkFrame(
                    command=packet[1],
                    sequence=packet[2],
                    payload=packet[5:-5],
                    integrity=int.from_bytes(packet[-5:-1], "little"),
                    packet_length=packet_length,
                )
            )
            del self._buffer[:packet_length]
        return tuple(frames)

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)


def iter_yk_frames(data: bytes) -> Iterable[YkFrame]:
    yield from YkFrameStreamDecoder().feed(data)


def _integrity_valid(packet: bytes) -> bool:
    integrity = int.from_bytes(packet[-5:-1], "little")
    if integrity == 0:
        return True
    expected = (_INTEGRITY_SEED + sum(packet[:-5]) + FRAME_END) & 0xFFFFFFFF
    return integrity == expected


def _byte(value: int, label: str) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"YK frame {label} must fit in uint8")
    return value


__all__ = [
    "FRAME_END",
    "FRAME_START",
    "SEQUENCE_MODULUS",
    "YkFrame",
    "YkFrameStreamDecoder",
    "iter_yk_frames",
    "pack_yk_frame",
    "resequence_yk_frame",
]
