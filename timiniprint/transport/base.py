from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TYPE_CHECKING

from ..protocol.job import ProtocolJob

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..devices.bluetooth_profiles import BleTransportProfile


class PrinterConnection(Protocol):
    """Active transport connection able to send stream-only ``ProtocolJob`` objects."""

    async def send(self, job: ProtocolJob) -> None: ...

    async def disconnect(self) -> None: ...


class RuntimeProbeConnection(PrinterConnection, Protocol):
    """Optional connection extension used by ``prepare_connection_runtime``."""

    @property
    def active_ble_profile(self) -> BleTransportProfile | None:
        """Applied GATT profile, or None for a non-BLE connection."""
        ...

    async def attach_runtime_controller(
        self,
        runtime_controller: object,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Stop/detach the previous controller in its owning event loop.

        Initialize on first attachment only. Replacements are already prepared;
        None detaches without installing a new receiver. Disconnect also stops
        the attached controller before releasing transport resources.
        """
        ...

    def can_send_control_packet(self) -> bool: ...

    def can_send_standard_payload(self) -> bool: ...

    def can_send_bulk_payload(self) -> bool: ...

    def can_query_control_packet(self) -> bool: ...

    def can_wait_for_reply(self) -> bool: ...

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

    async def wait_for_reply(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
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


class PrinterConnector(Protocol):
    """Transport factory that connects using a resolved ``PrinterDevice``."""

    async def connect(self, device: PrinterDevice) -> PrinterConnection: ...
