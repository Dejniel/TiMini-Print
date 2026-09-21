"""Quin reply framing with separate Phomemo and Print Master wire dialects."""

from __future__ import annotations

class _ReplyDecoder:
    """Retain partial frames and never reinterpret known reply data as opcodes."""

    # Sizes include the opcode, but not a framing prefix.
    _sizes: dict[int, int]
    _prefixes = (0x1A,)
    _allow_bare = False

    def __init__(self) -> None:
        self._pending = bytearray()

    @property
    def has_pending_frame(self) -> bool:
        return bool(self._pending)

    def feed(self, payload: bytes) -> list[bytes]:
        self._pending.extend(payload)
        frames = []
        while self._pending:
            start = 1 if self._pending[0] in self._prefixes else 0
            if not start and not self._allow_bare:
                del self._pending[0]
                continue
            if len(self._pending) <= start:
                break
            size = self._sizes.get(self._pending[start])
            if size is None:
                del self._pending[:start + 1]
                continue
            if len(self._pending) < start + size:
                break
            frames.append(bytes(self._pending[start:start + size]))
            del self._pending[:start + size]
        return frames


class PhomemoReplyDecoder(_ReplyDecoder):
    """Require 1A-prefixed frames, including six-byte capability replies."""

    _sizes = {
        0x03: 2, 0x04: 2, 0x05: 2, 0x06: 2, 0x07: 4, 0x08: 16,
        0x09: 2, 0x0B: 2, 0x0E: 2, 0x0F: 2, 0x16: 2, 0x17: 2,
        0x1D: 2, 0x20: 2, 0x35: 2, 0x3B: 6,
    }


class PrintMasterReplyDecoder(_ReplyDecoder):
    """Accept bare or single 1A/1B-prefixed frames and Print Master lengths."""

    _prefixes = (0x1A, 0x1B)
    _allow_bare = True
    _sizes = {
        0x03: 2, 0x04: 2, 0x05: 2, 0x06: 2, 0x07: 4, 0x08: 16,
        0x0B: 2, 0x0C: 2, 0x0F: 2, 0x15: 4, 0x17: 2, 0x31: 4,
        0x3B: 4, 0x3E: 2, 0x3F: 2, 0x40: 15,
    }


def phomemo_reply(data: bytes, opcode: int | tuple[int, ...] | None) -> bytes | None:
    """Find a complete reply; None accepts any recognized prefixed frame."""
    opcodes = (opcode,) if isinstance(opcode, int) else opcode
    result = None
    for frame in PhomemoReplyDecoder().feed(data):
        if opcodes is None or frame[0] in opcodes:
            result = frame[1:]
    return result
