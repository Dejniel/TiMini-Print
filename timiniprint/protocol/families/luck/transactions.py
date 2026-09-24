"""Reply contracts shared by Luck normal and AiYin control commands."""

from __future__ import annotations

from ...steps import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep

QUERY_TIMEOUT_SEC = 3.0
NORMAL_FINALIZE_TIMEOUT_SEC = 70.0
LUJIANG_A4_FINALIZE_TIMEOUT_SEC = 120.0
AIYIN_FINALIZE_TIMEOUT_SEC = 60.0


def _finalized(reply: bytes | None) -> bool:
    # A leading NUL is a status byte, not padding around a print ACK.
    return bool(reply and (reply.startswith(b"OK") or reply[0] == 0xAA))


_DENSITY_REPLY = ProtocolReplyMatcher(
    complete=lambda reply: len(reply) >= 2,
    matches=lambda reply: reply == b"OK",
)
_OK_PREFIX_REPLY = ProtocolReplyMatcher(
    complete=lambda reply: len(reply) >= 2,
    matches=lambda reply: bool(reply and reply.startswith(b"OK")),
)
# Busy, cover, paper, battery and the two overheating bits block a new job.
_STATUS_ERROR_MASK = 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x40
_STATUS_REPLY = ProtocolReplyMatcher(
    complete=bool,
    matches=lambda reply: bool(reply) and not reply[0] & _STATUS_ERROR_MASK,
)
_FINALIZE_REPLY = ProtocolReplyMatcher(complete=_finalized, matches=_finalized)


def status_query() -> ProtocolStep:
    # Charging (0x20) and the unused high bit do not make the printer busy.
    return ProtocolStep.query(
        "status", b"\x10\xff\x40",
        expect=ProtocolReplyExpectation.STATUS_ZERO,
        timeout_sec=QUERY_TIMEOUT_SEC, include_in_payload=False, reply_required=True,
        reply_matcher=_STATUS_REPLY,
    )


def density_setting(packet: bytes) -> ProtocolStep:
    return ProtocolStep.query(
        "density", packet, expect=ProtocolReplyExpectation.OK,
        timeout_sec=QUERY_TIMEOUT_SEC, reply_required=True,
        reply_matcher=_DENSITY_REPLY,
    )


def speed_setting(packet: bytes) -> ProtocolStep:
    return ProtocolStep.query(
        "speed", packet, expect=ProtocolReplyExpectation.OK,
        timeout_sec=QUERY_TIMEOUT_SEC, reply_required=True,
        reply_matcher=_OK_PREFIX_REPLY,
    )


def paper_setting(packet: bytes, *, wait_for_reply: bool) -> ProtocolStep:
    if not wait_for_reply:
        return ProtocolStep.send("paper type", packet)
    # An ignored setting result still reserves its response window. A missing
    # ACK must not skip the wait or be mistaken for the final print result.
    return ProtocolStep.query(
        "paper type", packet, expect=ProtocolReplyExpectation.OK,
        timeout_sec=QUERY_TIMEOUT_SEC,
        reply_matcher=_OK_PREFIX_REPLY,
    )


def finalize(packet: bytes, *, timeout_sec: float) -> ProtocolStep:
    return ProtocolStep.query(
        "finalize", packet, expect=ProtocolReplyExpectation.OK_OR_AA,
        timeout_sec=timeout_sec, reply_required=True,
        reply_matcher=_FINALIZE_REPLY,
    )
