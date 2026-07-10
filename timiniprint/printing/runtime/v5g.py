from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from ...devices.profiles import RuntimeSettings
from ...protocol.family import ProtocolFamily
from ...protocol.families.v5g import (
    V5G_CONNECT_QUERY_PACKET,
    V5G_TEMPERATURE_QUERY_PACKET,
    decode_density_payload,
    encode_density_payload,
)
from ...protocol.packet import (
    make_packet,
    prefixed_packet_opcode,
    prefixed_packet_payload,
    split_prefixed_packets,
)
from ...protocol.steps import ProtocolStepOperation
from .base import RuntimeController
from .v5g_density import (
    DensityLevels,
    V5GContinuousPlan,
    mx06_continuous_plan,
    mx06_single_density_value,
    mx10_continuous_plan,
    mx10_continuous_series,
    mx10_single_density_value,
    pd01_continuous_plan,
    pd01_continuous_series,
    pd01_single_density_value,
    v5g_continuous_series,
)


@dataclass
class _V5GSessionState:
    temperature_c: int = -1
    d2_status: bool = False
    didian_status: bool = False
    printing: bool = False
    helper_kind: Optional[str] = None
    profile_runtime_preset_key: Optional[str] = None
    last_complete_time: float = 0.0
    last_density_value: Optional[int] = None
    last_single_density_value: int = 0
    last_print_record_copies: int = 0
    last_print_record_density: Optional[int] = None
    last_print_mode_is_text: bool = False
    pending_reset_task: asyncio.Task | None = None


class V5GRuntimeController(RuntimeController):
    def __init__(
        self,
        *,
        runtime_settings: Optional[RuntimeSettings] = None,
    ) -> None:
        preset = None if runtime_settings is None else runtime_settings.preset
        self._state = _V5GSessionState(
            helper_kind=None if runtime_settings is None else runtime_settings.control_algorithm,
            profile_runtime_preset_key=None if preset is None else preset.key,
        )
        self._runtime_settings = runtime_settings

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if not isinstance(previous, V5GRuntimeController):
            return
        helper_kind = self._state.helper_kind
        profile_runtime_preset_key = self._state.profile_runtime_preset_key
        pending_reset_task = self._state.pending_reset_task
        runtime_settings = self._runtime_settings
        self._state = previous._state
        self._state.helper_kind = helper_kind or self._state.helper_kind
        self._state.profile_runtime_preset_key = profile_runtime_preset_key or self._state.profile_runtime_preset_key
        self._state.pending_reset_task = pending_reset_task
        self._runtime_settings = runtime_settings or previous._runtime_settings

    def debug_snapshot(self) -> dict[str, object]:
        density_levels = None
        preset = None if self._runtime_settings is None else self._runtime_settings.preset
        capabilities = None if self._runtime_settings is None else self._runtime_settings.capabilities
        if preset is not None and preset.density is not None:
            density_levels = {
                "image": {
                    "low": preset.density.image.low,
                    "middle": preset.density.image.middle,
                    "high": preset.density.image.high,
                },
                "text": {
                    "low": preset.density.text.low,
                    "middle": preset.density.text.middle,
                    "high": preset.density.text.high,
                },
            }
        return {
            "temperature_c": self._state.temperature_c,
            "d2_status": self._state.d2_status,
            "didian_status": self._state.didian_status,
            "printing": self._state.printing,
            "helper_kind": self._state.helper_kind,
            "profile_runtime_preset_key": self._state.profile_runtime_preset_key,
            "capabilities": {
                "d2_status": False if capabilities is None else capabilities.d2_status,
                "didian_status": False if capabilities is None else capabilities.didian_status,
            },
            "last_complete_time": self._state.last_complete_time,
            "last_density_value": self._state.last_density_value,
            "last_single_density_value": self._state.last_single_density_value,
            "last_print_record_copies": self._state.last_print_record_copies,
            "last_print_record_density": self._state.last_print_record_density,
            "last_print_mode_is_text": self._state.last_print_mode_is_text,
            "has_pending_reset_task": self._state.pending_reset_task is not None,
            "runtime_preset": None if preset is None else {"key": preset.key},
            "density_levels": density_levels,
        }

    def debug_update(self, **changes: object) -> None:
        for key, value in changes.items():
            if not hasattr(self._state, key):
                raise KeyError(f"Unknown V5G debug field '{key}'")
            setattr(self._state, key, value)

    async def initialize_connection(self, session, *, mtu_size: int, timeout: float) -> None:
        _ = mtu_size
        sent = await session.send_control_packet(V5G_CONNECT_QUERY_PACKET, timeout=timeout)
        if not sent:
            raise RuntimeError("V5G connect query send unavailable")

    async def probe_capabilities(self, session, *, timeout: float) -> None:
        if not session.can_send_control_packet_wait_notification():
            return
        await session.send_control_packet_wait_notification(
            V5G_TEMPERATURE_QUERY_PACKET,
            label="v5g temperature",
            match=lambda payload: prefixed_packet_opcode(payload, ProtocolFamily.V5G) == 0xD3,
            timeout=min(timeout, 0.4),
            required=False,
        )

    async def stop(self, session) -> None:
        if self._state.pending_reset_task is None:
            return
        self._state.pending_reset_task.cancel()
        self._state.pending_reset_task = None

    async def send_protocol_steps(self, session, steps, *, timeout: float) -> bool:
        _ = timeout
        if not session.can_send_standard_payload():
            return False
        if any(step.operation is not ProtocolStepOperation.SEND for step in steps):
            return False
        data = b"".join(step.data for step in steps)
        self._state.printing = True
        try:
            data = self._prepare_v5g_standard_payload(session, data)
            await session.send_standard_payload(data)
        finally:
            self._state.printing = False
            self._state.last_complete_time = time.time()
        return True

    def handle_notification(self, session, payload: bytes) -> None:
        opcode = prefixed_packet_opcode(payload, ProtocolFamily.V5G)
        if opcode == 0xA3:
            self._update_status(session, payload)
        elif opcode == 0xD2:
            self._update_d2_status(session, payload)
        elif opcode == 0xD3:
            self._update_temperature(session, payload)

    def _select_levels(self, *, is_text: bool) -> DensityLevels | None:
        preset = None if self._runtime_settings is None else self._runtime_settings.preset
        if preset is None or preset.density is None:
            return None
        source = preset.density.text if is_text else preset.density.image
        return DensityLevels(low=source.low, middle=source.middle, high=source.high)

    def _prepare_v5g_standard_payload(self, session, data: bytes) -> bytes:
        if len(data) <= 50:
            return data
        packets = split_prefixed_packets(data, ProtocolFamily.V5G)
        if packets is None:
            return data
        density_indexes = [
            index for index, packet in enumerate(packets)
            if prefixed_packet_opcode(packet, ProtocolFamily.V5G) == 0xF2
        ]
        if not density_indexes:
            return data
        if self._should_use_continuous_helper(session, packets, density_indexes):
            rewrite_map = self._build_continuous_density_map(session, packets, density_indexes)
        else:
            rewrite_map = self._build_single_density_map(session, packets, density_indexes)

        updated = bytearray()
        current_mode_is_text = self._state.last_print_mode_is_text
        last_density_value = self._state.last_density_value
        for index, packet in enumerate(packets):
            opcode = prefixed_packet_opcode(packet, ProtocolFamily.V5G)
            if opcode == 0xBE:
                current_mode_is_text = self._extract_print_mode(session, packet)
            if index in rewrite_map:
                packet = make_packet(
                    0xF2,
                    encode_density_payload(rewrite_map[index]),
                    ProtocolFamily.V5G,
                )
                last_density_value = rewrite_map[index]
            elif opcode == 0xF2:
                current_value = self._extract_density_value(session, packet)
                if current_value is not None:
                    last_density_value = current_value
            updated += packet
        self._state.last_density_value = last_density_value
        self._state.last_print_mode_is_text = current_mode_is_text
        if not rewrite_map:
            return data
        return bytes(updated)

    def _should_use_continuous_helper(self, session, packets: list[bytes], density_indexes: list[int]) -> bool:
        if len(density_indexes) <= 4:
            return False
        first_index = density_indexes[0]
        first_value = self._extract_density_value(session, packets[first_index])
        current_mode_is_text = self._mode_before_packet_index(session, packets, first_index)
        levels, _mode_is_text = self._levels_for_density_value(
            first_value,
            fallback_is_text=current_mode_is_text,
        )
        if levels is None or first_value is None:
            return False
        helper_kind = self._state.helper_kind
        qualifies = helper_kind in {"mx06", "mx10", "pd01"} or first_value >= levels.middle
        if not qualifies:
            return False
        if helper_kind in {"mx10", "pd01"}:
            return True
        return self._supports_d2_status()

    def _build_single_density_map(self, session, packets: list[bytes], density_indexes: list[int]) -> dict[int, int]:
        first_index = density_indexes[0]
        current_mode_is_text = self._mode_before_packet_index(session, packets, first_index)
        current_value = self._extract_density_value(session, packets[first_index])
        levels, current_mode_is_text = self._levels_for_density_value(
            current_value,
            fallback_is_text=current_mode_is_text,
        )
        if current_value is None or levels is None:
            return {}

        adjusted = current_value
        helper_kind = self._state.helper_kind
        recent_completion = (time.time() - self._state.last_complete_time) < 50
        temperature_c = self._state.temperature_c
        if helper_kind == "mx06" and self._state.d2_status and recent_completion:
            adjusted = mx06_single_density_value(current_value, self._state.last_single_density_value)
        elif helper_kind == "pd01" and temperature_c >= 50:
            adjusted = pd01_single_density_value(temperature_c, levels, current_value)
        elif helper_kind == "mx10" and temperature_c >= 50:
            adjusted = mx10_single_density_value(temperature_c, levels, current_value)

        self._state.last_single_density_value = adjusted
        if adjusted == current_value:
            return {}
        session.report_debug(
            f"V5G single density adjusted mode={'text' if current_mode_is_text else 'image'} "
            f"user={current_value} target={adjusted} temp={self._state.temperature_c}"
        )
        return {density_index: adjusted for density_index in density_indexes}

    def _build_continuous_density_map(
        self,
        session,
        packets: list[bytes],
        density_indexes: list[int],
    ) -> dict[int, int]:
        first_index = density_indexes[0]
        current_mode_is_text = self._mode_before_packet_index(session, packets, first_index)
        first_value = self._extract_density_value(session, packets[first_index])
        levels, current_mode_is_text = self._levels_for_density_value(
            first_value,
            fallback_is_text=current_mode_is_text,
        )
        if levels is None or first_value is None:
            return {}
        helper_kind = self._state.helper_kind
        temperature_c = self._state.temperature_c
        if helper_kind == "mx06":
            plan = mx06_continuous_plan(
                levels,
                first_value,
                last_record_density=self._state.last_print_record_density,
                recent_completion=(time.time() - self._state.last_complete_time) < 50,
            )
        elif helper_kind == "pd01":
            plan = pd01_continuous_plan(temperature_c, levels, first_value)
        elif helper_kind == "mx10":
            plan = mx10_continuous_plan(temperature_c, levels, first_value)
        else:
            plan = V5GContinuousPlan(
                begin_density_value=min(levels.middle, first_value),
                unchanged_packet_count=4,
                minimum_density_value=95,
                update_first_packet=min(levels.middle, first_value) != first_value,
            )

        rewrite_map: dict[int, int] = {}
        leading_value = plan.begin_density_value if plan.update_first_packet else first_value
        leading_count = min(len(density_indexes), plan.unchanged_packet_count)
        for density_index in density_indexes[:leading_count]:
            current_value = self._extract_density_value(session, packets[density_index])
            if current_value != leading_value:
                rewrite_map[density_index] = leading_value

        remaining = max(0, len(density_indexes) - plan.unchanged_packet_count)
        sequence: list[int] = []
        if remaining > 0:
            if helper_kind == "pd01":
                sequence = pd01_continuous_series(leading_value, remaining)
            elif helper_kind == "mx10":
                sequence = mx10_continuous_series(
                    leading_value,
                    remaining,
                    minimum_value=plan.minimum_density_value,
                )
            else:
                sequence = v5g_continuous_series(
                    leading_value,
                    remaining,
                    clamp_low_70=plan.clamp_low_70,
                )

        for offset, density_index in enumerate(density_indexes[plan.unchanged_packet_count:]):
            if offset >= len(sequence):
                break
            current_value = self._extract_density_value(session, packets[density_index])
            if current_value != sequence[offset]:
                rewrite_map[density_index] = sequence[offset]

        final_density = sequence[-1] if sequence else leading_value
        self._state.last_print_record_copies = len(density_indexes)
        self._state.last_print_record_density = final_density
        session.report_debug(
            f"V5G continuous density helper kind={self._state.helper_kind} "
            f"count={len(density_indexes)} first={leading_value} temp={self._state.temperature_c}"
        )
        return rewrite_map

    def _mode_before_packet_index(self, session, packets: list[bytes], packet_index: int) -> bool:
        is_text = self._state.last_print_mode_is_text
        for packet in packets[:packet_index]:
            if prefixed_packet_opcode(packet, ProtocolFamily.V5G) == 0xBE:
                is_text = self._extract_print_mode(session, packet)
        return is_text

    def _levels_for_density_value(
        self,
        value: int | None,
        *,
        fallback_is_text: bool,
    ) -> tuple[DensityLevels | None, bool]:
        if value is None:
            return self._select_levels(is_text=fallback_is_text), fallback_is_text
        text_levels = self._select_levels(is_text=True)
        image_levels = self._select_levels(is_text=False)
        text_score = self._density_level_distance(text_levels, value)
        image_score = self._density_level_distance(image_levels, value)
        if text_score is None and image_score is None:
            return None, fallback_is_text
        if image_score is None or (text_score is not None and text_score < image_score):
            return text_levels, True
        if text_score is None or image_score < text_score:
            return image_levels, False
        return self._select_levels(is_text=fallback_is_text), fallback_is_text

    @staticmethod
    def _density_level_distance(levels: DensityLevels | None, value: int) -> int | None:
        if levels is None:
            return None
        return min(
            abs(value - levels.low),
            abs(value - levels.middle),
            abs(value - levels.high),
        )

    def _supports_d2_status(self) -> bool:
        if self._runtime_settings is None:
            return False
        return self._runtime_settings.capabilities.d2_status

    @staticmethod
    def _extract_density_value(session, packet: bytes) -> int | None:
        payload = prefixed_packet_payload(packet, ProtocolFamily.V5G)
        if payload is None or len(payload) != 2:
            return None
        return decode_density_payload(payload)

    @staticmethod
    def _extract_print_mode(session, packet: bytes) -> bool:
        payload = prefixed_packet_payload(packet, ProtocolFamily.V5G)
        if not payload:
            return False
        return payload[0] == 0x01

    def _update_status(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5G)
        if not raw:
            return
        status = raw[0]
        if status == 0x00:
            self._state.didian_status = False
        elif status == 0x08:
            self._state.didian_status = True
        elif status == 0x04:
            self._state.d2_status = True
        session.report_debug(
            f"V5G status status=0x{status:02x} didian={self._state.didian_status} d2={self._state.d2_status}"
        )

    def _update_d2_status(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5G)
        if raw is None:
            return
        self._state.d2_status = True
        session.report_debug("V5G D2 status received")

    def _update_temperature(self, session, payload: bytes) -> None:
        raw = prefixed_packet_payload(payload, ProtocolFamily.V5G)
        if not raw:
            return
        previous = self._state.temperature_c
        self._state.temperature_c = -1 if raw[0] == 0xFF else raw[0]
        if (
            self._state.helper_kind == "pd01"
            and not self._state.printing
            and (
                self._state.temperature_c == -1
                or (
                    previous >= 0
                    and self._state.temperature_c < previous
                    and self._state.temperature_c <= 60
                )
            )
        ):
            self._schedule_density_reset(session, 120)
        session.report_debug(f"V5G temperature={self._state.temperature_c}")

    def _schedule_density_reset(self, session, value: int) -> None:
        if self._state.pending_reset_task is not None and not self._state.pending_reset_task.done():
            return
        if not session.can_send_control_packet():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._state.pending_reset_task = loop.create_task(self._send_density_reset(session, value))
        self._state.pending_reset_task.add_done_callback(
            lambda _task: setattr(self._state, "pending_reset_task", None)
        )

    async def _send_density_reset(self, session, value: int) -> None:
        packet = make_packet(
            0xF2,
            encode_density_payload(value),
            ProtocolFamily.V5G,
        )
        await session.send_control_packet(packet, timeout=0.2)
        self._state.last_density_value = value
