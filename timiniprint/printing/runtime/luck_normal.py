from __future__ import annotations

from dataclasses import dataclass

from .base import RuntimeController, RuntimePrintCapabilities, RuntimeSessionApi

LUCK_MODEL_QUERY_PACKET = bytes([0x10, 0xFF, 0x20, 0xF0])
LUCK_DENSITY_PREFIX = bytes([0x10, 0xFF, 0x10, 0x00])
LUCK_STATUS_QUERY_PACKET = bytes([0x10, 0xFF, 0x40])
LUCK_PAPER_TYPE_PREFIX = bytes([0x1F, 0x80, 0x01])
LUCK_OK_REPLY = b"OK"
LUCK_QUERY_DRIVEN_VARIANTS = {"lujiang_normal", "lujiang_normal_h"}
LUCK_BITMAP_PREFIXES = (
    bytes([0x1D, 0x76, 0x30, 0x00]),
    bytes([0x1D, 0x47, 0x59]),
    bytes([0x1F, 0x10]),
)


@dataclass
class _LuckNormalProbeState:
    protocol_variant: str
    probed_model: str | None = None
    capabilities: RuntimePrintCapabilities | None = None
    degraded_warning_emitted: bool = False
    print_flow_warning_emitted: bool = False


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

        reply = await session.query_control_packet(LUCK_MODEL_QUERY_PACKET, timeout=timeout)
        if not reply:
            self._warn_degraded(session, reason="model query returned no reply")
            self._state.capabilities = RuntimePrintCapabilities(
                supports_gray=False,
                gray_level_override=gray_level_override,
            )
            return

        model_name = reply.decode("gb2312", errors="ignore").replace("\x00", "").strip()
        self._state.probed_model = model_name
        self._state.capabilities = RuntimePrintCapabilities(
            supports_gray=bool(model_name) and model_name.endswith("_GY"),
            gray_level_override=gray_level_override,
        )

    def runtime_capabilities(self) -> RuntimePrintCapabilities | None:
        return self._state.capabilities

    async def send_standard_job_payload(
        self,
        session: RuntimeSessionApi,
        data: bytes,
        *,
        timeout: float,
    ) -> bool:
        if self._state.protocol_variant not in LUCK_QUERY_DRIVEN_VARIANTS:
            return False
        if not session.can_query_control_packet():
            self._warn_print_flow(
                session,
                "Luck print ACK/status query unavailable",
                "PPA2L/PPA2LH needs runtime ACK/status queries before the bitmap. "
                "The current transport cannot do that, so the job will be sent in degraded stream-only mode.",
            )
            return False

        remaining = data
        if remaining.startswith(LUCK_DENSITY_PREFIX) and len(remaining) >= len(LUCK_DENSITY_PREFIX) + 1:
            density_packet = remaining[: len(LUCK_DENSITY_PREFIX) + 1]
            await self._query_expect_ok(session, density_packet, "density", timeout=timeout)
            remaining = remaining[len(density_packet) :]

        await self._query_status(session, timeout=timeout)

        command_prefix, bitmap_and_tail = self._split_before_bitmap(remaining)
        paper_type_offset = command_prefix.find(LUCK_PAPER_TYPE_PREFIX)
        if paper_type_offset < 0:
            await session.send_standard_payload(remaining)
            return True

        before_paper_type = command_prefix[:paper_type_offset]
        paper_type_packet = command_prefix[paper_type_offset : paper_type_offset + len(LUCK_PAPER_TYPE_PREFIX) + 1]
        after_paper_type = command_prefix[paper_type_offset + len(paper_type_packet) :] + bitmap_and_tail
        if len(paper_type_packet) != len(LUCK_PAPER_TYPE_PREFIX) + 1:
            await session.send_standard_payload(remaining)
            return True

        if before_paper_type:
            await session.send_standard_payload(before_paper_type)
        await self._query_expect_ok(session, paper_type_packet, "paper type", timeout=timeout)
        if after_paper_type:
            await session.send_standard_payload(after_paper_type)
        return True

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "protocol_variant": self._state.protocol_variant,
            "probed_model": self._state.probed_model,
            "capabilities": None
            if self._state.capabilities is None
            else {
                "supports_gray": self._state.capabilities.supports_gray,
                "gray_level_override": self._state.capabilities.gray_level_override,
            },
            "degraded_warning_emitted": self._state.degraded_warning_emitted,
            "print_flow_warning_emitted": self._state.print_flow_warning_emitted,
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

    async def _query_expect_ok(
        self,
        session: RuntimeSessionApi,
        packet: bytes,
        label: str,
        *,
        timeout: float,
    ) -> None:
        reply = await session.query_control_packet(packet, timeout=timeout)
        if self._reply_is_ok(reply):
            return
        self._warn_print_flow(
            session,
            f"Luck {label} ACK missing",
            (
                f"Luck {label} command did not return OK before printing. "
                "Continuing, but PPA2L/PPA2LH may ignore the job."
            ),
        )

    async def _query_status(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        reply = await session.query_control_packet(LUCK_STATUS_QUERY_PACKET, timeout=timeout)
        if reply and reply[0] == 0:
            return
        if reply:
            status = f"0x{reply[0]:02x}"
        else:
            status = "no reply"
        self._warn_print_flow(
            session,
            "Luck printer status not clear",
            f"Luck status query before printing returned {status}. Continuing, but the printer may reject the job.",
        )

    @staticmethod
    def _reply_is_ok(reply: bytes | None) -> bool:
        return bool(reply and reply.replace(b"\x00", b"").startswith(LUCK_OK_REPLY))

    @staticmethod
    def _split_before_bitmap(data: bytes) -> tuple[bytes, bytes]:
        offsets = [offset for prefix in LUCK_BITMAP_PREFIXES if (offset := data.find(prefix)) >= 0]
        if not offsets:
            return data, b""
        bitmap_offset = min(offsets)
        return data[:bitmap_offset], data[bitmap_offset:]

    def _warn_print_flow(self, session: RuntimeSessionApi, short: str, detail: str) -> None:
        if self._state.print_flow_warning_emitted:
            return
        self._state.print_flow_warning_emitted = True
        session.report_warning(short=short, detail=detail)
