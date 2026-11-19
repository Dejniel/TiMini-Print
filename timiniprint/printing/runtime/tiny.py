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

    def handle_notification(
        self,
        session: RuntimeSessionApi,
        payload: bytes,
    ) -> None:
        packets = self._decoder.feed(payload)
        if payload and not packets:
            state = "incomplete" if self._decoder.pending else "unrecognized"
            self._log_rx(session, f"Tiny {state} RX", payload, changes=False)
        for packet in packets:
            if packet.opcode != 0xAE or packet.flags != 1:
                continue
            if packet.payload == b"\x10":
                self._set_flow_paused(session, True, payload=packet.raw)
            elif packet.payload == b"\x00":
                self._set_flow_paused(session, False, payload=packet.raw)


__all__ = ["TinyRuntimeController"]
