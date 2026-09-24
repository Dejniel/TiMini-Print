from __future__ import annotations

from dataclasses import dataclass, replace

from ...devices import PrinterDevice
from ...devices.profiles import LevelProfile, ModeLevelProfile, SpeedProfile
from ...protocol import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from ...protocol.runtime import RuntimePrintCapabilities
from ...protocol.families.luck.transactions import QUERY_TIMEOUT_SEC
from ..errors import PrinterNotReadyError
from ..step_execution import ProtocolReplyError, execute_protocol_step, execute_protocol_steps
from .a4_status import query_status_error
from .base import PreparedPrinter, RuntimeController, RuntimeSessionApi

LUCK_MODEL_QUERY_PACKET = bytes([0x10, 0xFF, 0x20, 0xF0])
LUCK_VERSION_QUERY_PACKET = bytes([0x10, 0xFF, 0x20, 0xF1])
_STRING_PADDING = "".join(chr(value) for value in range(0x21))
# Model/firmware text has no length field: collect the entire response window.
_TEXT_REPLY = ProtocolReplyMatcher(complete=lambda reply: False)


async def query_luck_text(
    session: RuntimeSessionApi, packet: bytes, label: str, *, timeout: float,
) -> str | None:
    """Read an optional Luck identity string over either session reply channel."""
    if not (session.can_query_control_packet() or session.can_send_control_packet_wait_notification()):
        return None
    try:
        reply = await execute_protocol_step(
            session,
            ProtocolStep.query(
                label, packet, expect=ProtocolReplyExpectation.NONE,
                reply_matcher=_TEXT_REPLY,
                timeout_sec=min(timeout, QUERY_TIMEOUT_SEC), include_in_payload=False,
            ),
            timeout=timeout, log_prefix="Luck",
        )
    except TimeoutError:
        return None
    return reply.decode("gb2312", errors="replace").strip(_STRING_PADDING) if reply is not None else None


@dataclass
class _LuckNormalProbeState:
    protocol_variant: str
    probed_model: str | None = None
    firmware_version: str | None = None
    capabilities: RuntimePrintCapabilities | None = None
    degraded_warning_emitted: bool = False


class LuckTransactionController(RuntimeController):
    """Execute normal/A4/AiYin steps and expose reported printer conditions."""

    async def send_protocol_steps(
        self, session: RuntimeSessionApi, steps: tuple[ProtocolStep, ...], *, timeout: float,
    ) -> bool:
        try:
            return await execute_protocol_steps(session, steps, timeout=timeout, log_prefix="Luck")
        except ProtocolReplyError as exc:
            if exc.step.expect is ProtocolReplyExpectation.STATUS_ZERO:
                error = self._status_error(exc.reply)
                if error is not None:
                    raise error from exc
            raise

    def _status_error(self, reply: bytes | None) -> PrinterNotReadyError | None:
        return query_status_error(
            reply, device_label="Luck", extra_overheat_mask=0x40, ignored_mask=0xA0,
        )


class LuckA41RuntimeController(LuckTransactionController):
    """Resolve A41 controls once per connection, without changing catalog objects."""

    def __init__(self) -> None:
        self._firmware_version: str | None = None

    async def prepare(
        self, device: PrinterDevice, session: RuntimeSessionApi, *, timeout: float,
    ) -> PreparedPrinter:
        self._firmware_version = await query_luck_text(
            session, LUCK_VERSION_QUERY_PACKET, "firmware", timeout=timeout,
        )

        levels = LevelProfile(0, 7, 15)
        speed = None
        if self._firmware_version is None:
            session.report_warning(
                short="Luck A41 firmware unavailable",
                detail="Keeping density 0..15 (default 7) and speed disabled for this connection.",
            )
        else:
            # This gate deliberately compares normalized text, not semantic versions.
            version = self._firmware_version.lower().replace("v", "")
            if version < "1.26":
                levels = LevelProfile(0, 1, 2)
            else:
                levels = LevelProfile(1, 8, 15)
                speed = device.profile.speed or SpeedProfile(image=4, text=4)
        defaults = replace(device.profile.print_defaults,
                           density=ModeLevelProfile(levels, levels), speed=speed)
        profile = replace(device.profile, print_defaults=defaults)
        session.report_debug(f"Luck A41 firmware={self._firmware_version!r} "
                             f"density={levels.low}..{levels.high} default={levels.middle} speed={speed}")
        return PreparedPrinter(device.with_print_profile(profile), self)

    def debug_snapshot(self) -> dict[str, object]:
        return {"firmware_version": self._firmware_version}


class LuckNormalRuntimeController(LuckTransactionController):
    def __init__(self, *, protocol_variant: str) -> None:
        self._state = _LuckNormalProbeState(protocol_variant=protocol_variant)

    async def prepare(
        self,
        device: PrinterDevice,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> PreparedPrinter:
        self._state = _LuckNormalProbeState(protocol_variant=self._state.protocol_variant)
        model_name = await query_luck_text(
            session, LUCK_MODEL_QUERY_PACKET, "model", timeout=timeout,
        )
        self._state.probed_model = model_name
        if model_name is None:
            self._warn_degraded(session, reason="model query returned no reply")
        else:
            self._state.firmware_version = await query_luck_text(
                session, LUCK_VERSION_QUERY_PACKET, "firmware", timeout=timeout,
            )
            if self._state.firmware_version is not None:
                session.report_debug(f"Luck firmware: version={self._state.firmware_version}")
        self._state.capabilities = RuntimePrintCapabilities(
            supports_gray=bool(model_name) and model_name.endswith("_GY"),
            gray_level_override=self._gray_level_override(),
        )
        return PreparedPrinter(device, self, self._state.capabilities)

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
                "This Luck printer is running in degraded mono-only mode because the live model probe "
                f"failed ({reason}). Gray printing will not work in this session. This is likely a "
                "program limitation, please report it."
            ),
        )
