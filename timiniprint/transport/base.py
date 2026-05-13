from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TYPE_CHECKING

from ..protocol.job import ProtocolJob

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..printing.runtime.base import RuntimeController


class PrinterConnection(Protocol):
    """Active transport connection able to send ``ProtocolJob`` objects."""

    async def send(self, job: ProtocolJob) -> None: ...

    async def disconnect(self) -> None: ...


class RuntimeProbeConnection(PrinterConnection, Protocol):
    """Optional connection extension used by ``prepare_connection_runtime``."""

    async def attach_runtime_controller(
        self,
        runtime_controller: RuntimeController,
        *,
        timeout: float = 1.0,
    ) -> None: ...

    def can_send_control_packet(self) -> bool: ...

    def can_query_control_packet(self) -> bool: ...

    def can_wait_for_notification(self) -> bool: ...

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

    async def send_standard_payload(self, data: bytes) -> None: ...


class PrinterConnector(Protocol):
    """Transport factory that connects using a resolved ``PrinterDevice``."""

    async def connect(self, device: PrinterDevice) -> PrinterConnection: ...
