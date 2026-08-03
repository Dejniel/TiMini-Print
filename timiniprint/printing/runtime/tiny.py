from __future__ import annotations

from ...protocol.family import ProtocolFamily
from ...protocol.packet import PrefixedPacketStreamDecoder
from .base import RuntimeController, RuntimeSessionApi


class TinyRuntimeController(RuntimeController):
    """Apply buffer flow-control notifications from the Tiny command dialect."""

    def __init__(self) -> None:
        # Both Tiny outbound prefix variants receive buffer notifications in
        # the standard 51 78 packet dialect.
        self._decoder = PrefixedPacketStreamDecoder(ProtocolFamily.TINY)

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, TinyRuntimeController):
            self._decoder = previous._decoder

    def handle_notification(
        self,
        session: RuntimeSessionApi,
        payload: bytes,
    ) -> None:
        for packet in self._decoder.feed(payload):
            if packet.opcode != 0xAE or packet.flags != 1:
                continue
            if packet.payload == b"\x10":
                session.set_flow_paused(True, payload=packet.raw)
            elif packet.payload == b"\x00":
                session.set_flow_paused(False, payload=packet.raw)


__all__ = ["TinyRuntimeController"]
