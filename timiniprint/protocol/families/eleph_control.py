"""Shared JX information and status records for Eleph ESC and TSPL."""

from __future__ import annotations

from dataclasses import dataclass

from ..status import PrinterStatusCode


DEVICE_INFO_QUERY = b"\x1b##JXIG"
DEVICE_INFO_LENGTH = 116
STATUS_QUERY = b"\x1d\x61\x00"
STATUS_LENGTH = 4
ERROR_PREFIX = b"err:"
REPRINT_PROMPT = b"\x1b#RP"


@dataclass(frozen=True)
class ElephDeviceInfo:
    width: int
    dpi_type: int
    command_mode: int
    speed: int
    density: int
    compression: bool
    paper_type: int
    version: int

    @classmethod
    def parse(cls, data: bytes) -> ElephDeviceInfo:
        if len(data) < DEVICE_INFO_LENGTH:
            raise ValueError("Eleph device information requires 116 bytes")
        # The reader does not validate the settings-writer checksum or packLen.
        return cls(
            width=int.from_bytes(data[8:10], "little"),
            dpi_type=data[10], command_mode=data[11], speed=data[14], density=data[15],
            compression=data[22] == 1, paper_type=data[17],
            version=int.from_bytes(data[6:8], "little"),
        )

    @property
    def dpi(self) -> int:
        if self.dpi_type not in (0, 1, 2):
            raise ValueError(f"Unknown Eleph DPI type: {self.dpi_type}")
        return round((8, 12, 24)[self.dpi_type] * 25.4)


@dataclass(frozen=True)
class ElephStatus:
    raw: bytes

    @classmethod
    def parse(cls, data: bytes) -> ElephStatus:
        if len(data) != STATUS_LENGTH:
            raise ValueError("Eleph status requires four bytes")
        return cls(bytes(data))

    @property
    def error_codes(self) -> tuple[PrinterStatusCode, ...]:
        b0, b1, b2, _ = self.raw
        codes = []
        if b0 & 0x20:
            codes.append(PrinterStatusCode.COVER_OPEN)
        if b2 & 0x0C == 0x0C:
            codes.append(PrinterStatusCode.PAPER_OUT)
        if b1 & 0x40:
            codes.append(PrinterStatusCode.OVERHEATED)
        if b1 & 0x08:
            codes.append(PrinterStatusCode.CUTTER_ERROR)
        return tuple(codes)


class ElephReplyDecoder:
    """Separate owned JX records from framed, possibly fragmented events."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    @staticmethod
    def _frame_size(data: bytes | bytearray) -> int | None:
        if data.startswith(ERROR_PREFIX):
            return len(ERROR_PREFIX) + STATUS_LENGTH
        if data.startswith(REPRINT_PROMPT):
            return len(REPRINT_PROMPT)
        if any(prefix.startswith(data) for prefix in (ERROR_PREFIX, REPRINT_PROMPT)):
            return None
        return 0

    @classmethod
    def query_reply(cls, packet: bytes, data: bytes) -> tuple[bytes | None, bytes]:
        """Return the owned record and remaining event bytes without consuming state."""
        if packet == DEVICE_INFO_QUERY:
            if len(data) < DEVICE_INFO_LENGTH:
                return None, b""
            return data[:DEVICE_INFO_LENGTH], data[DEVICE_INFO_LENGTH:]
        if packet != STATUS_QUERY:
            raise ValueError("Unknown Eleph query")
        offset = 0
        while offset < len(data):
            remaining = data[offset:]
            size = cls._frame_size(remaining)
            if size is None or size > len(remaining):
                return None, data
            if size == len(REPRINT_PROMPT):
                offset += size
                continue
            if size:
                record = remaining[len(ERROR_PREFIX):size]
            elif len(remaining) >= STATUS_LENGTH:
                size = STATUS_LENGTH
                record = remaining[:size]
            else:
                return None, data[:offset]
            return record, data[:offset] + remaining[size:]
        return None, data

    def feed(self, payload: bytes) -> tuple[ElephStatus | bytes, ...]:
        self._buffer.extend(payload)
        frames = []
        while self._buffer:
            size = self._frame_size(self._buffer)
            if size is None:
                break
            if not size:
                del self._buffer[0]
                continue
            if len(self._buffer) < size:
                break
            frame = bytes(self._buffer[:size])
            frames.append(ElephStatus.parse(frame[len(ERROR_PREFIX):])
                          if size > len(REPRINT_PROMPT) else frame)
            del self._buffer[:size]
        return tuple(frames)
