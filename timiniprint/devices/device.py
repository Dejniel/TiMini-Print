from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Optional

from ..protocol.family import ProtocolFamily
from ..protocol.types import ImagePipelineConfig

if TYPE_CHECKING:
    from .profiles import PrinterProfile, RuntimeSettings


class BluetoothEndpointTransport(str, Enum):
    """Bluetooth transport flavor used by a discovered endpoint."""

    CLASSIC = "classic"
    BLE = "ble"


@dataclass(frozen=True)
class BluetoothEndpoint:
    """One concrete Bluetooth endpoint returned by discovery."""

    name: str
    address: str
    paired: Optional[bool] = None
    transport: BluetoothEndpointTransport = BluetoothEndpointTransport.CLASSIC


@dataclass(frozen=True)
class BluetoothTarget:
    """Bluetooth transport target, optionally combining classic and BLE endpoints."""

    classic_endpoint: Optional[BluetoothEndpoint]
    ble_endpoint: Optional[BluetoothEndpoint]
    display_address: str
    transport_badge: str

    @property
    def paired(self) -> Optional[bool]:
        paired_states = []
        if self.classic_endpoint is not None:
            paired_states.append(self.classic_endpoint.paired)
        if self.ble_endpoint is not None:
            paired_states.append(self.ble_endpoint.paired)
        if any(state is True for state in paired_states):
            return True
        if any(state is False for state in paired_states):
            return False
        return None

    def ordered_endpoints(self, *, prefer_spp: bool) -> list[BluetoothEndpoint]:
        """Return endpoints in the preferred connection order for this device."""
        ordered = []
        transports = (
            (BluetoothEndpointTransport.CLASSIC, self.classic_endpoint),
            (BluetoothEndpointTransport.BLE, self.ble_endpoint),
        )
        if not prefer_spp:
            transports = tuple(reversed(transports))
        for _transport, endpoint in transports:
            if endpoint is not None:
                ordered.append(endpoint)
        return ordered


@dataclass(frozen=True)
class SerialTarget:
    """Serial transport target used by serial connectors."""

    path: str
    baud_rate: int = 115200


TransportTarget = BluetoothTarget | SerialTarget


@dataclass(frozen=True)
class PrinterDevice:
    """Concrete runtime printer description shared by protocol and transport."""

    display_name: str
    profile: "PrinterProfile"
    protocol_family: ProtocolFamily
    protocol_variant: str | None
    image_pipeline: ImagePipelineConfig
    runtime_settings: "RuntimeSettings | None" = None
    transport_target: TransportTarget | None = None
    detection_rule_key: str = ""

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def profile_key(self) -> str:
        return self.profile.profile_key

    @property
    def address(self) -> str:
        target = self.transport_target
        if isinstance(target, BluetoothTarget):
            return target.display_address
        if isinstance(target, SerialTarget):
            return target.path
        return ""

    @property
    def paired(self) -> Optional[bool]:
        target = self.transport_target
        if isinstance(target, BluetoothTarget):
            return target.paired
        return None

    @property
    def transport_badge(self) -> str:
        target = self.transport_target
        if isinstance(target, BluetoothTarget):
            return target.transport_badge
        if isinstance(target, SerialTarget):
            return "[serial]"
        return ""

    def with_transport_target(self, transport_target: TransportTarget | None) -> "PrinterDevice":
        """Return a copy of this device with a different transport target."""
        return replace(self, transport_target=transport_target)
