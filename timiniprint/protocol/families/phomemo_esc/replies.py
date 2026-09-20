"""Incremental Quin/Print Master replies, independent of transport read boundaries."""

from __future__ import annotations

# Sizes include the opcode, but not the optional single 1A/1B prefix.
_REPLY_SIZES = {
    0x03: 2, 0x04: 2, 0x05: 2, 0x06: 2, 0x07: 4, 0x08: 16,
    0x0B: 2, 0x0C: 2, 0x0F: 2, 0x15: 4, 0x17: 2, 0x31: 4,
    0x3B: 4, 0x3E: 2, 0x3F: 2, 0x40: 15,
}


class PrintMasterReplyDecoder:
    """Retain partial frames and never reinterpret known reply data as opcodes."""

    def __init__(self) -> None:
        self._pending = bytearray()

    @property
    def has_pending_frame(self) -> bool:
        return bool(self._pending)

    def feed(self, payload: bytes) -> list[bytes]:
        self._pending.extend(payload)
        frames = []
        while self._pending:
            start = 1 if self._pending[0] in (0x1A, 0x1B) else 0
            if len(self._pending) <= start:
                break
            size = _REPLY_SIZES.get(self._pending[start])
            if size is None:
                del self._pending[:start + 1]
                continue
            if len(self._pending) < start + size:
                break
            frames.append(bytes(self._pending[start:start + size]))
            del self._pending[:start + size]
        return frames
