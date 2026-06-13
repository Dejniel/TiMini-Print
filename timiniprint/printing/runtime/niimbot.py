from __future__ import annotations

from dataclasses import dataclass

from ...protocol.families.niimbot.core import (
    NiimbotResponse,
    connect_packet,
    model_id_from_reply,
    model_id_query_packet,
    protocol_version_from_status_data,
    response_matcher,
    status_data_query_packet,
)
from .base import RuntimeController, RuntimeSessionApi


@dataclass
class _NiimbotProbeState:
    model_id: int | None = None
    protocol_version: int | None = None
    warning_emitted: bool = False


class NiimbotRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._state = _NiimbotProbeState()

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, NiimbotRuntimeController):
            self._state = previous._state

    async def probe_capabilities(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        connect_reply = await self._query(
            session,
            "connect",
            connect_packet(),
            NiimbotResponse.CONNECT,
            timeout=timeout,
        )
        if connect_reply is None:
            self._warn_probe_unavailable(session, reason="connect reply missing")
            return

        status_reply = await self._query(
            session,
            "status data",
            status_data_query_packet(),
            NiimbotResponse.PRINTER_STATUS_DATA,
            timeout=timeout,
        )
        self._state.protocol_version = protocol_version_from_status_data(status_reply)

        model_reply = await self._query(
            session,
            "model id",
            model_id_query_packet(),
            NiimbotResponse.PRINTER_INFO_MODEL_ID,
            timeout=timeout,
        )
        self._state.model_id = model_id_from_reply(model_reply)
        session.report_debug(
            "NIIMBOT probe: "
            f"model_id={self._state.model_id if self._state.model_id is not None else '<unknown>'} "
            f"protocol_version={self._state.protocol_version if self._state.protocol_version is not None else '<unknown>'}"
        )

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "model_id": self._state.model_id,
            "protocol_version": self._state.protocol_version,
            "warning_emitted": self._state.warning_emitted,
        }

    async def _query(
        self,
        session: RuntimeSessionApi,
        label: str,
        packet: bytes,
        expected: NiimbotResponse,
        *,
        timeout: float,
    ) -> bytes | None:
        matcher = response_matcher(expected)
        if session.can_query_control_packet():
            reply = await session.query_control_packet(
                packet,
                timeout=timeout,
                reply_complete=matcher.complete,
            )
        elif session.can_send_control_packet_wait_notification():
            reply = await session.send_control_packet_wait_notification(
                packet,
                label=f"NIIMBOT {label}",
                match=matcher.complete,
                timeout=timeout,
                required=False,
            )
        else:
            reply = None
        session.report_debug(
            f"NIIMBOT query {label}: tx={_hex_preview(packet)} rx={_hex_preview(reply)}"
        )
        return reply

    def _warn_probe_unavailable(self, session: RuntimeSessionApi, *, reason: str) -> None:
        if self._state.warning_emitted:
            return
        self._state.warning_emitted = True
        session.report_warning(
            short="NIIMBOT probe unavailable",
            detail=(
                "NIIMBOT live model probe failed "
                f"({reason}). Printing may still work for an explicit model profile, but "
                "auto task selection is limited in this session."
            ),
        )


def _hex_preview(data: bytes | None) -> str:
    if data is None:
        return "<none>"
    if not data:
        return "<empty>"
    if len(data) <= 32:
        return data.hex(" ")
    return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
