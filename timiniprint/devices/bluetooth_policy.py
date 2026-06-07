from __future__ import annotations

from collections.abc import Iterable

from .bluetooth_resolver import BluetoothEndpointResolver, ResolvedBluetoothTarget
from .catalog import PrinterCatalog
from .device import BluetoothEndpoint, BluetoothTarget, PrinterDevice


class BluetoothTransportPolicy:
    """Backend-neutral Bluetooth decisions shared by desktop and mobile transports."""

    def __init__(self, catalog: PrinterCatalog) -> None:
        self._resolver = BluetoothEndpointResolver(catalog)

    def devices_from_endpoints(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> list[PrinterDevice]:
        return self._resolver.devices_from_endpoints(endpoints)

    def transport_targets_from_endpoints(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> list[ResolvedBluetoothTarget]:
        return self._resolver.transport_targets_from_endpoints(endpoints)

    def should_retry_ble_scan(self, endpoints: Iterable[BluetoothEndpoint]) -> bool:
        targets = self.transport_targets_from_endpoints(endpoints)
        return any(
            item.transport_target.classic_endpoint is not None
            and item.transport_target.ble_endpoint is None
            for item in targets
        )

    @staticmethod
    def ordered_connection_endpoints(device: PrinterDevice) -> list[BluetoothEndpoint]:
        return ordered_connection_endpoints(device)


def should_retry_ble_scan(
    catalog: PrinterCatalog,
    endpoints: Iterable[BluetoothEndpoint],
) -> bool:
    return BluetoothTransportPolicy(catalog).should_retry_ble_scan(endpoints)


def ordered_connection_endpoints(device: PrinterDevice) -> list[BluetoothEndpoint]:
    target = device.transport_target
    if not isinstance(target, BluetoothTarget):
        raise RuntimeError("Bluetooth connection requires a PrinterDevice with BluetoothTarget")
    return target.ordered_endpoints(prefer_spp=device.profile.use_spp)


__all__ = [
    "BluetoothTransportPolicy",
    "ordered_connection_endpoints",
    "should_retry_ble_scan",
]
