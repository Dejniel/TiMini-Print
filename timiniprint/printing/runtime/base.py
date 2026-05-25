from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class RuntimePrintCapabilities:
    """Session-derived print capabilities that affect payload construction."""

    supports_gray: bool | None = None
    gray_level_override: int | None = None


@dataclass(frozen=True)
class PreparedRuntimeContext:
    """Prepared live-session state reused by later protocol job builds."""

    runtime_controller: "RuntimeController | None" = None
    capabilities: RuntimePrintCapabilities | None = None


class RuntimeSessionApi(Protocol):
    notify_started: bool

    def make_packet(self, opcode: int, payload: bytes) -> bytes: ...

    def split_prefixed_packets(self, data: bytes) -> list[bytes] | None: ...

    def extract_prefixed_opcode(self, payload: bytes) -> int | None: ...

    def extract_prefixed_payload(self, packet: bytes) -> bytes | None: ...

    def report_debug(self, message: str) -> None: ...

    def report_warning(self, *, short: str, detail: str) -> None: ...

    def can_send_control_packet(self) -> bool: ...
    def can_query_control_packet(self) -> bool: ...
    def can_wait_for_notification(self) -> bool: ...
    def can_send_control_packet_wait_notification(self) -> bool: ...

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool: ...

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None: ...

    async def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None: ...

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match: Callable[[bytes], bool],
        timeout: float,
        required: bool = True,
    ) -> bytes | None: ...

    async def send_standard_payload(self, data: bytes) -> None: ...


class RuntimeController:
    def adopt_previous(self, previous: "RuntimeController | None") -> None:
        return None

    async def initialize_connection(
        self,
        session: RuntimeSessionApi,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        return None

    async def after_initialize(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        return None

    async def stop(self, session: RuntimeSessionApi) -> None:
        return None

    async def probe_capabilities(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        return None

    def runtime_capabilities(self) -> RuntimePrintCapabilities | None:
        return None

    def prepare_standard_payload(self, session: RuntimeSessionApi, data: bytes) -> bytes:
        return data

    def on_standard_send_started(self, session: RuntimeSessionApi) -> None:
        return None

    def on_standard_send_finished(self, session: RuntimeSessionApi) -> None:
        return None

    def track_outgoing_query_status(self, session: RuntimeSessionApi, data: bytes) -> None:
        return None

    # TODO: These split/ACK hooks are the older BLE runtime path for V5X/V5G/V5C
    # notification/flow-control behavior. Do not add new protocol sequencing here.
    # Future cleanup should model both SPP replies and BLE notifications as
    # explicit "send, then wait until condition" runtime steps. Keep the two
    # transports separate underneath: SPP uses reply-matched control queries,
    # BLE uses notification/event-matched waits. New request/response families
    # should expose named ProtocolJob.steps instead of adding more hooks here.
    def build_split_context(self, session: RuntimeSessionApi, split: Any) -> Any:
        return None

    def prepare_split_command(
        self,
        session: RuntimeSessionApi,
        packet: bytes,
        split_context: Any,
    ) -> tuple[bytes | None, bool]:
        return packet, False

    async def before_split_command(
        self,
        session: RuntimeSessionApi,
        packet: bytes,
        split_context: Any,
        *,
        timeout: float,
        density_updated: bool,
    ) -> None:
        return None

    def arm_command_ack(self, session: RuntimeSessionApi, packet: bytes) -> Any:
        return None

    async def after_split_command(
        self,
        session: RuntimeSessionApi,
        packet: bytes,
        split_context: Any,
        *,
        timeout: float,
        density_updated: bool,
        ack_token: Any,
    ) -> None:
        return None

    def clear_command_ack(self, session: RuntimeSessionApi, ack_token: Any) -> None:
        return None

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        return None

    def build_compat_request(self, **kwargs) -> Optional[dict[str, str]]:
        return None

    def apply_compat_result(self, session: RuntimeSessionApi, **kwargs) -> None:
        return None

    def debug_snapshot(self) -> dict[str, Any]:
        return {}

    def debug_update(self, **changes: Any) -> None:
        if changes:
            unknown = ", ".join(sorted(changes.keys()))
            raise KeyError(f"Runtime controller does not support debug_update fields: {unknown}")
