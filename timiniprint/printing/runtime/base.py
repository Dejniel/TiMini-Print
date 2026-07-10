from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol

from ...protocol.runtime import RuntimePrintCapabilities

if TYPE_CHECKING:
    from ...protocol import ProtocolStep


@dataclass(frozen=True)
class PreparedRuntimeContext:
    """Prepared live-session state reused by later protocol job builds."""

    runtime_controller: "RuntimeController | None" = None
    capabilities: RuntimePrintCapabilities | None = None


class RuntimeSessionApi(Protocol):
    def report_debug(self, message: str) -> None: ...

    def report_warning(self, *, short: str, detail: str) -> None: ...

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None: ...

    def can_send_control_packet(self) -> bool: ...
    def can_query_control_packet(self) -> bool: ...
    def can_wait_for_notification(self) -> bool: ...
    def can_send_control_packet_wait_notification(self) -> bool: ...
    def can_send_standard_payload(self) -> bool: ...
    def can_send_bulk_payload(self) -> bool: ...

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

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool: ...


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

    async def wait_for_completion(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        """Wait for the device to finish the job before the caller disconnects.

        Default is a no-op. Families whose hardware keeps working after the last
        byte is sent (e.g. V5X, which keeps printing for several seconds) override
        this to hold the link until the device reports done, so closing the
        transport early does not truncate the output.
        """
        return None

    async def send_protocol_steps(
        self,
        session: RuntimeSessionApi,
        steps: tuple["ProtocolStep", ...],
        *,
        timeout: float,
    ) -> bool:
        return False

    async def probe_capabilities(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        return None

    def runtime_capabilities(self) -> RuntimePrintCapabilities | None:
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
