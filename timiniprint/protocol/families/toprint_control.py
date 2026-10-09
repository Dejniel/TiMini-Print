"""Media selection and unsolicited status replies shared by ToPrint dialects."""

from __future__ import annotations

from dataclasses import dataclass

from ..types import PaperMode


_PAPER_TYPES = {PaperMode.PLAIN: 1, PaperMode.TAG: 2, PaperMode.BLACK_TAG: 3}


def paper_type_cmd(paper_mode: PaperMode | None) -> bytes:
    return b"\x10\xff\x10\x03" + bytes((_PAPER_TYPES[paper_mode or PaperMode.TAG],))


@dataclass(frozen=True)
class ToPrintStatus:
    raw: int

    @property
    def errors(self) -> tuple[str, ...]:
        errors = []
        if self.raw & 0x01:
            errors.append("paper_out")
        if self.raw & 0x02:
            errors.append("cover_open")
        # Low battery is a composite mask, not an independent bit 2.
        if self.raw & 0x05 == 0x05:
            errors.append("low_battery")
        return tuple(errors)

    @property
    def printing(self) -> bool:
        return bool(self.raw & 0x20)

    @property
    def paused(self) -> bool:
        return bool(self.raw & 0x10)


class ToPrintReplyDecoder:
    """Buffer AA mode replies; each other byte is an unsolicited status."""

    def __init__(self) -> None:
        self._mode_prefix = False

    def feed(self, payload: bytes) -> tuple[ToPrintStatus | bytes, ...]:
        replies: list[ToPrintStatus | bytes] = []
        for value in payload:
            if self._mode_prefix:
                replies.append(bytes((0xAA, value)))
                self._mode_prefix = False
            elif value == 0xAA:
                self._mode_prefix = True
            else:
                replies.append(ToPrintStatus(value))
        return tuple(replies)
