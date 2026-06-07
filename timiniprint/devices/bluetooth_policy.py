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

    def failure_message(
        self,
        failures: Iterable[tuple[BluetoothConnectionAttempt, Exception]],
    ) -> str:
        failure_list = list(failures)
        if not failure_list:
            return "Bluetooth connection failed"
        if len(failure_list) == 1:
            attempt, error = failure_list[0]
            return (
                "Bluetooth connection failed "
                f"({attempt.endpoint.transport.value} error: {error})"
            )
        parts = []
        for attempt, error in failure_list:
            suffix = "fallback error" if attempt.is_fallback else "error"
            parts.append(f"{attempt.endpoint.transport.value} {suffix}: {error}")
        return "Bluetooth connection failed (" + "; ".join(parts) + ")"


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

    @staticmethod
    def connection_plan(device: PrinterDevice) -> BluetoothConnectionPlan:
        return bluetooth_connection_plan(device)


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
    "ordered_connection_endpoints",
    "should_retry_ble_scan",
]
