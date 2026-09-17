from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Optional, Tuple, Union

from ..protocol.family import ProtocolFamily
from ..protocol.types import ImagePipelineConfig
from .bluetooth_profiles import BleTransportProfile, get_ble_transport_profile

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


TransportTarget = Union[BluetoothTarget, SerialTarget]


@dataclass(frozen=True)
class PrinterDevice:
    """Immutable printer description, not a live connection or status snapshot.

    The profile supplies geometry/defaults; the family, variant and image
    pipeline select the recipe. ``transport_target=None`` allows offline job
    building but supplies no connection destination. ``model_key`` identifies
    a catalog model and can be empty for profile-only construction.

    Discovery may return provisional geometry. After connecting, use the device
    returned by ``ConnectedPrinter.printer_device()`` for printing and controls.
    """

    display_name: str
    profile: "PrinterProfile"
    protocol_family: ProtocolFamily
    protocol_variant: Optional[str]
    image_pipeline: ImagePipelineConfig
    runtime_settings: Optional["RuntimeSettings"] = None
    transport_target: Optional[TransportTarget] = None
    model_key: str = ""
    origin_ids: Tuple[str, ...] = ()

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

    @property
    def ble_transport_profile(self) -> BleTransportProfile:
        return get_ble_transport_profile(self.protocol_family)

    def with_transport_target(self, transport_target: Optional[TransportTarget]) -> "PrinterDevice":
        """Return a copy of this device with a different transport target."""
        return replace(self, transport_target=transport_target)

    def with_protocol_variant(self, variant: str | None) -> "PrinterDevice":
        """Select one effective variant, keeping the exported profile consistent."""
        return replace(
            self,
            protocol_variant=variant,
            profile=replace(
                self.profile,
                protocol_default=replace(
                    self.profile.protocol_default,
                    type=self.protocol_family,
                    packets_type=variant,
                ),
            ),
        )

    def with_print_profile(
        self,
        profile: "PrinterProfile",
        *,
        image_pipeline: ImagePipelineConfig | None = None,
    ) -> "PrinterDevice":
        """Replace print defaults without changing this device's effective recipe.

        Geometry/profile refinements must not reset model-level protocol or
        raster overrides to the catalog template's defaults.
        """
        pipeline = image_pipeline if image_pipeline is not None else self.image_pipeline
        profile = replace(
            profile,
            protocol_default=replace(
                profile.protocol_default,
                type=self.protocol_family,
                packets_type=self.protocol_variant,
            ),
            default_image_pipeline=pipeline,
        )
        return replace(self, profile=profile, image_pipeline=pipeline)

    def for_connection(self, connected_device: "PrinterDevice") -> "PrinterDevice":
        """Bind a selected catalog device to an already open connection.

        Keep its print recipe, but retain the connection's address and stream
        settings. Preparation must separately validate GATT compatibility.
        """
        return replace(
            self,
            display_name=connected_device.display_name,
            transport_target=connected_device.transport_target,
            profile=replace(
                self.profile,
                use_spp=connected_device.profile.use_spp,
                stream=connected_device.profile.stream,
                ble_mtu_request=connected_device.profile.ble_mtu_request,
            ),
        )
