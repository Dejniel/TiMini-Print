from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TYPE_CHECKING

from ..protocol.job import ProtocolJob

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..devices.bluetooth_profiles import BleTransportProfile


class ConnectionCleanupError(RuntimeError):
    """Cause marking a failed open/preparation whose cleanup also failed.

    The original operation exception is re-raised with this marker as its
    cause; this marker's cause retains the actual cleanup exception. Stop
    reconnecting rather than interpreting any chained exception as unsafe
    cleanup. A successful disconnect is not implied by the operation failing.
    """

    def __init__(self, error: BaseException) -> None:
        super().__init__("Printer connection cleanup failed")
        self.__cause__ = error

    @classmethod
    def is_cause_of(cls, error: BaseException) -> bool:
        """Recognize unsafe cleanup, including through timeout/cancellation wrappers.

        Only an explicit marker counts; an unrelated chained exception does not.
        """
        seen = set()
        cause: BaseException | None = error
        while cause is not None and id(cause) not in seen:
            if isinstance(cause, cls):
                return True
            seen.add(id(cause))
            cause = cause.__cause__
        return False


class PrinterConnection(Protocol):
    """Active transport connection able to send stream-only ``ProtocolJob`` objects."""

    async def send(self, job: ProtocolJob) -> None: ...

    async def disconnect(self) -> None: ...


class ControlConnection(Protocol):
    """Raw transactions shared by runtime preparation and profile-free probing.

    No job execution or runtime-controller ownership is required. Capability
    checks describe the actual open transport, not the printer's protocol.
    """

    @property
    def active_ble_profile(self) -> BleTransportProfile | None:
        """Applied GATT profile, or None for a non-BLE connection."""
        ...

    async def disconnect(self) -> None: ...

    def can_send_control_packet(self) -> bool: ...

    def can_send_standard_payload(self) -> bool: ...

    def can_query_control_packet(self) -> bool: ...

    def can_wait_for_reply(self) -> bool: ...

    def can_send_control_packet_wait_notification(self) -> bool: ...

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool: ...

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        """Collect a reply until complete or timeout; partial bytes are not success.

        The caller validates the reply. Never combine separate BLE channels.
        """
        ...

    async def wait_for_reply(
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


class RuntimeProbeConnection(ControlConnection, PrinterConnection, Protocol):
    """Print connection extension owning a prepared runtime controller."""

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

    def can_send_bulk_payload(self) -> bool: ...

    def can_wait_for_notification(self) -> bool: ...

    async def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None: ...

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool: ...


class PrinterConnector(Protocol):
    """Transport factory that connects using a resolved ``PrinterDevice``.

    A failed open must release its resources. If cleanup fails, re-raise the
    operation exception from ConnectionCleanupError(cleanup_error), so callers
    can distinguish it from an ordinary connection error and stop reconnecting.
    """

    async def connect(self, device: PrinterDevice) -> PrinterConnection: ...
