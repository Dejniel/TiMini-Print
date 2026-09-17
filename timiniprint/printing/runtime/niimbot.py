from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ...protocol.families.niimbot.core import (
    NiimbotConnectResult,
    NiimbotResponse,
    connect_packet,
    connect_result_from_reply,
    model_id_from_reply,
    model_id_query_packet,
    protocol_version_from_status_data,
    response_matcher,
    status_data_query_packet,
)
from .base import PreparedPrinter, RuntimeController, RuntimeSessionApi

if TYPE_CHECKING:
    from ...devices import PrinterDevice


@dataclass
class _NiimbotProbeState:
    connect_result: NiimbotConnectResult | None = None
    model_id: int | None = None
    protocol_version: int | None = None
    warning_emitted: bool = False


class NiimbotRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._state = _NiimbotProbeState()

    async def prepare(
        self,
        device: PrinterDevice,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> PreparedPrinter:
        await self._query_printer(session, timeout=timeout)
        return PreparedPrinter(device, self)

    async def _query_printer(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        self._state = _NiimbotProbeState()
        connect_reply = await self._query(
            session,
            "connect",
            connect_packet(),
            NiimbotResponse.CONNECT,
            timeout=timeout,
        )
        result = connect_result_from_reply(connect_reply)
        self._state.connect_result = result
        if result not in {
            NiimbotConnectResult.CONNECTED,
            NiimbotConnectResult.CONNECTED_NEW,
            NiimbotConnectResult.CONNECTED_V3,
        }:
            self._warn_probe_unavailable(session, reason="missing or unsuccessful connect result")
            return

        if result is NiimbotConnectResult.CONNECTED_V3:
            status_reply = await self._query(
                session,
                "status data",
                status_data_query_packet(),
                NiimbotResponse.PRINTER_STATUS_DATA,
                timeout=timeout,
            )
            self._state.protocol_version = protocol_version_from_status_data(status_reply)
        else:
            self._state.protocol_version = (
                1 if result is NiimbotConnectResult.CONNECTED_NEW else 0
            )

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
            f"connect_result={result.name} "
            f"model_id={self._state.model_id if self._state.model_id is not None else '<unknown>'} "
            f"protocol_version={self._state.protocol_version if self._state.protocol_version is not None else '<unknown>'}"
        )

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "connect_result": (
                int(self._state.connect_result)
                if self._state.connect_result is not None else None
            ),
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


class VersionedNiimbotRuntimeController(NiimbotRuntimeController):
    def __init__(self, *, variants: Mapping[int, str], fallback_variant: str) -> None:
        super().__init__()
        self._variants = dict(variants)
        self._fallback_variant = fallback_variant

    async def prepare(
        self,
        device: PrinterDevice,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> PreparedPrinter:
        await self._query_printer(session, timeout=timeout)
        variant = self._variants.get(self._state.protocol_version, self._fallback_variant)
        return PreparedPrinter(device.with_protocol_variant(variant), self)

    def debug_snapshot(self) -> dict[str, object]:
        snapshot = super().debug_snapshot()
        snapshot["resolved_variant"] = self._variants.get(
            self._state.protocol_version, self._fallback_variant
        )
        return snapshot


def _hex_preview(data: bytes | None) -> str:
    if data is None:
        return "<none>"
    if not data:
        return "<empty>"
    if len(data) <= 32:
        return data.hex(" ")
    return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
