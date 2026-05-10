from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProtocolStepOperation(str, Enum):
    SEND = "send"
    QUERY = "query"


class ProtocolReplyExpectation(str, Enum):
    NONE = "none"
    OK = "ok"
    STATUS_ZERO = "status_zero"
    OK_OR_AA = "ok_or_aa"


@dataclass(frozen=True)
class ProtocolStep:
    """One protocol-level operation in a printable job."""

    label: str
    data: bytes
    operation: ProtocolStepOperation = ProtocolStepOperation.SEND
    expect: ProtocolReplyExpectation = ProtocolReplyExpectation.NONE
    timeout_sec: float | None = None
    include_in_payload: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        object.__setattr__(self, "operation", ProtocolStepOperation(self.operation))
        object.__setattr__(self, "expect", ProtocolReplyExpectation(self.expect))

    @classmethod
    def send(cls, label: str, data: bytes) -> "ProtocolStep":
        return cls(label=label, data=data, operation=ProtocolStepOperation.SEND)

    @classmethod
    def query(
        cls,
        label: str,
        data: bytes,
        *,
        expect: ProtocolReplyExpectation,
        timeout_sec: float | None = None,
        include_in_payload: bool = True,
    ) -> "ProtocolStep":
        return cls(
            label=label,
            data=data,
            operation=ProtocolStepOperation.QUERY,
            expect=expect,
            timeout_sec=timeout_sec,
            include_in_payload=include_in_payload,
        )


def reply_matches_expectation(expect: ProtocolReplyExpectation, reply: bytes | None) -> bool:
    expectation = ProtocolReplyExpectation(expect)
    if expectation is ProtocolReplyExpectation.NONE:
        return True
    if expectation is ProtocolReplyExpectation.OK:
        return _reply_is_ok(reply)
    if expectation is ProtocolReplyExpectation.STATUS_ZERO:
        return bool(reply and reply[0] == 0)
    if expectation is ProtocolReplyExpectation.OK_OR_AA:
        return _reply_is_ok(reply) or bool(reply and reply[0] == 0xAA)
    raise ValueError(f"Unsupported protocol reply expectation: {expectation.value}")


def _reply_is_ok(reply: bytes | None) -> bool:
    return bool(reply and reply.replace(b"\x00", b"").startswith(b"OK"))
