from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .bluetooth_resolver import BluetoothEndpointResolver, ResolvedBluetoothTarget
from .catalog import PrinterCatalog
from .device import BluetoothEndpoint, BluetoothTarget, PrinterDevice


@dataclass(frozen=True)
class BluetoothConnectionAttempt:
    """One planned Bluetooth connection attempt with backend-neutral metadata."""

    endpoint: BluetoothEndpoint
    pairing_hint: bool
    index: int
    total: int

    @property
    def is_fallback(self) -> bool:
        return self.index > 1

    @property
    def transport_label(self) -> str:
        return self.endpoint.transport.value


@dataclass(frozen=True)
class BluetoothConnectionPlan:
    """Ordered connection attempts for one resolved Bluetooth printer."""

    device: PrinterDevice
    attempts: tuple[BluetoothConnectionAttempt, ...]
    pairing_hint: bool

    @property
    def endpoints(self) -> tuple[BluetoothEndpoint, ...]:
        return tuple(attempt.endpoint for attempt in self.attempts)

    def describe(self) -> str:
        if not self.attempts:
            return "none"
        return ", ".join(
            f"{attempt.endpoint.transport.value}({attempt.endpoint.address})"
            for attempt in self.attempts
        )


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
    def connection_plan(device: PrinterDevice) -> BluetoothConnectionPlan:
        return bluetooth_connection_plan(device)


def bluetooth_connection_plan(device: PrinterDevice) -> BluetoothConnectionPlan:
    target = device.transport_target
    if not isinstance(target, BluetoothTarget):
        raise RuntimeError("Bluetooth connection requires a PrinterDevice with BluetoothTarget")
    endpoints = tuple(target.ordered_endpoints(prefer_spp=device.profile.use_spp))
    pairing_hint = target.paired is False
    attempts = tuple(
        BluetoothConnectionAttempt(
            endpoint=endpoint,
            pairing_hint=pairing_hint,
            index=index,
            total=len(endpoints),
        )
        for index, endpoint in enumerate(endpoints, start=1)
    )
    return BluetoothConnectionPlan(
        device=device,
        attempts=attempts,
        pairing_hint=pairing_hint,
    )


__all__ = [
    "BluetoothConnectionAttempt",
    "BluetoothConnectionPlan",
    "BluetoothTransportPolicy",
    "bluetooth_connection_plan",
]
