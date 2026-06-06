from __future__ import annotations

from dataclasses import dataclass

from ...protocol.runtime import RuntimePrintCapabilities
from .base import RuntimeController, RuntimeSessionApi

LUCK_MODEL_QUERY_PACKET = bytes([0x10, 0xFF, 0x20, 0xF0])
LUCK_VERSION_QUERY_PACKET = bytes([0x10, 0xFF, 0x20, 0xF1])


@dataclass
class _LuckNormalProbeState:
    protocol_variant: str
    probed_model: str | None = None
    firmware_version: str | None = None
    capabilities: RuntimePrintCapabilities | None = None
    degraded_warning_emitted: bool = False


class LuckNormalRuntimeController(RuntimeController):
    def __init__(self, *, protocol_variant: str) -> None:
        self._state = _LuckNormalProbeState(protocol_variant=protocol_variant)

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if not isinstance(previous, LuckNormalRuntimeController):
            return
        if previous._state.protocol_variant != self._state.protocol_variant:
            return
        self._state = previous._state

    async def probe_capabilities(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        gray_level_override = self._gray_level_override()
        if not session.can_query_control_packet():
            self._warn_degraded(session, reason="query transport is unavailable")
            self._state.capabilities = RuntimePrintCapabilities(
                supports_gray=False,
                gray_level_override=gray_level_override,
            )
            return

        reply = await self._query_logged(
            session,
            LUCK_MODEL_QUERY_PACKET,
            "model",
            timeout=timeout,
        )
        if not reply:
            self._warn_degraded(session, reason="model query returned no reply")
            self._state.capabilities = RuntimePrintCapabilities(
                supports_gray=False,
                gray_level_override=gray_level_override,
            )
            return

        model_name = reply.decode("gb2312", errors="ignore").replace("\x00", "").strip()
        self._state.probed_model = model_name
        version_reply = await self._query_logged(
            session,
            LUCK_VERSION_QUERY_PACKET,
            "firmware",
            timeout=timeout,
        )
        if version_reply:
            self._state.firmware_version = (
                version_reply.decode("gb2312", errors="ignore").replace("\x00", "").strip()
            )
            session.report_debug(f"Luck firmware: version={self._state.firmware_version}")
        self._state.capabilities = RuntimePrintCapabilities(
            supports_gray=bool(model_name) and model_name.endswith("_GY"),
            gray_level_override=gray_level_override,
        )

    def runtime_capabilities(self) -> RuntimePrintCapabilities | None:
        return self._state.capabilities

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "protocol_variant": self._state.protocol_variant,
            "probed_model": self._state.probed_model,
            "firmware_version": self._state.firmware_version,
            "capabilities": None
            if self._state.capabilities is None
            else {
                "supports_gray": self._state.capabilities.supports_gray,
                "gray_level_override": self._state.capabilities.gray_level_override,
            },
            "degraded_warning_emitted": self._state.degraded_warning_emitted,
        }

    def _gray_level_override(self) -> int | None:
        if self._state.protocol_variant == "lujiang_normal_h":
            return 12
        return None

    def _warn_degraded(self, session: RuntimeSessionApi, *, reason: str) -> None:
        if self._state.degraded_warning_emitted:
            return
        self._state.degraded_warning_emitted = True
        session.report_warning(
            short="Luck capability probe unavailable",
            detail=(
                "PPA2L/PPA2LH is running in degraded mono-only mode because the live Luck model probe "
                f"failed ({reason}). Gray printing will not work in this session. This is likely a "
                "program limitation, please report it."
            ),
        )

    async def _query_logged(
        self,
        session: RuntimeSessionApi,
        packet: bytes,
        label: str,
        *,
        timeout: float,
    ) -> bytes | None:
        reply = await session.query_control_packet(packet, timeout=timeout)
        session.report_debug(
            f"Luck query {label}: tx={self._hex_preview(packet)} rx={self._hex_preview(reply)}"
        )
        return reply

    @staticmethod
    def _hex_preview(data: bytes | None) -> str:
        if data is None:
            return "<none>"
        if not data:
            return "<empty>"
        if len(data) <= 32:
            return data.hex(" ")
        return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
