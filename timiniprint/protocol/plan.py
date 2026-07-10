from __future__ import annotations

from dataclasses import dataclass

from .steps import ProtocolStep


@dataclass(frozen=True)
class ProtocolPlan:
    """Stateless wire payload and optional ordered protocol operations."""

    payload: bytes
    steps: tuple[ProtocolStep, ...] = ()

    def __post_init__(self) -> None:
        payload = bytes(self.payload)
        steps = tuple(self.steps)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "steps", steps)
        if steps:
            steps_payload = b"".join(
                step.data for step in steps if step.include_in_payload
            )
            if payload != steps_payload:
                raise ValueError(
                    "Protocol plan payload does not match included protocol steps"
                )

    @classmethod
    def stream(cls, payload: bytes) -> "ProtocolPlan":
        return cls(payload=payload)

    @classmethod
    def sequence(cls, steps: tuple[ProtocolStep, ...]) -> "ProtocolPlan":
        normalized_steps = tuple(steps)
        return cls(
            payload=b"".join(
                step.data for step in normalized_steps if step.include_in_payload
            ),
            steps=normalized_steps,
        )
