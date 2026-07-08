from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from ... import reporting
from ...devices import (
    BluetoothEndpointResolver,
    BluetoothTarget,
    BluetoothTransportPolicy,
    PrinterCatalog,
    PrinterDevice,
    ResolvedBluetoothTarget,
    SupportedModelMatch,
    UnsupportedModelMatch,
)
from ...devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from .backend import SppBackend
from .types import DeviceInfo, DeviceTransport, ScanFailure


@dataclass(frozen=True)
class BluetoothScanResult:
    """Resolved Bluetooth scan output with logical devices and scan failures."""

    devices: List[PrinterDevice]
    failures: List[ScanFailure]
    raw_endpoints: List[DeviceInfo] = field(default_factory=list)


class BluetoothDiscovery:
    """Discover reachable Bluetooth printers and resolve them into devices."""

    def __init__(
        self,
        catalog: PrinterCatalog,
        reporter: reporting.Reporter | None = None,
    ) -> None:
        self._catalog = catalog
        self._resolver = BluetoothEndpointResolver(catalog)
        self._policy = BluetoothTransportPolicy(catalog)
        self._reporter = reporter

    async def _scan_endpoints(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> tuple[List[DeviceInfo], List[ScanFailure]]:
        devices, failures = await SppBackend.scan_with_failures(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        if include_classic and include_ble:
            if self._policy.should_retry_ble_scan(self._endpoints_from_scan(devices)):
                ble_devices, _failures = await SppBackend.scan_with_failures(
                    timeout=timeout,
                    include_classic=False,
                    include_ble=True,
                )
                devices = DeviceInfo.dedupe(list(devices) + list(ble_devices))
        return devices, failures

    def _scan_endpoints_blocking(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> tuple[List[DeviceInfo], List[ScanFailure]]:
        devices, failures = SppBackend.scan_with_failures_blocking(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        if include_classic and include_ble:
            if self._policy.should_retry_ble_scan(self._endpoints_from_scan(devices)):
                ble_devices, _failures = SppBackend.scan_with_failures_blocking(
                    timeout=timeout,
                    include_classic=False,
                    include_ble=True,
                )
                devices = DeviceInfo.dedupe(list(devices) + list(ble_devices))
        return devices, failures

    async def scan_report(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> BluetoothScanResult:
        """Scan Bluetooth and return logical devices plus transport scan failures."""
        devices, failures = await self._scan_endpoints(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        resolved = self.devices_from_scan(devices)
        self._report_scan_debug(devices, resolved, failures)
        return BluetoothScanResult(devices=resolved, failures=failures, raw_endpoints=devices)

    def scan_report_blocking(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> BluetoothScanResult:
        """Blocking scan variant for UI workers that must not use asyncio executors."""
        devices, failures = self._scan_endpoints_blocking(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        resolved = self.devices_from_scan(devices)
        self._report_scan_debug(devices, resolved, failures)
        return BluetoothScanResult(devices=resolved, failures=failures, raw_endpoints=devices)

    async def scan_devices(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> List[PrinterDevice]:
        """Scan Bluetooth and return resolved printer devices only."""
        result = await self.scan_report(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        return result.devices

    def devices_from_scan(self, devices: Iterable[DeviceInfo]) -> List[PrinterDevice]:
        """Resolve raw scan endpoints into logical printer devices."""
        return self._policy.devices_from_endpoints(self._endpoints_from_scan(devices))

    def devices_for_display(self, result: BluetoothScanResult) -> List[PrinterDevice]:
        """Return scan devices plus manual source-app candidates for UI/CLI lists."""
        return self._resolver.devices_for_display(
            result.devices,
            self._endpoints_from_scan(result.raw_endpoints),
        )

    def manual_targets_for_display(self, result: BluetoothScanResult) -> List[ResolvedBluetoothTarget]:
        """Return scanned raw targets that need a manual model choice."""
        return self._resolver.manual_targets_for_display(
            result.devices,
            self._endpoints_from_scan(result.raw_endpoints),
        )

    def _transport_targets_from_scan(
        self,
        devices: Iterable[DeviceInfo],
    ) -> List[ResolvedBluetoothTarget]:
        """Resolve raw scan endpoints into logical Bluetooth transport targets."""
        return self._policy.transport_targets_from_endpoints(self._endpoints_from_scan(devices))

    async def resolve_device(
        self,
        name_or_address: Optional[str],
        transport: Optional[DeviceTransport] = None,
    ) -> PrinterDevice:
        """Scan Bluetooth and pick one resolved device by name, address, or default."""
        if transport == DeviceTransport.CLASSIC:
            devices = (
                await self.scan_report(
                    include_classic=True,
                    include_ble=False,
                )
            ).devices
        elif transport == DeviceTransport.BLE:
            devices = (
                await self.scan_report(
                    include_classic=False,
                    include_ble=True,
                )
            ).devices
        else:
            devices = (
                await self.scan_report(
                    include_classic=True,
                    include_ble=True,
                )
            ).devices
        if not devices:
            raise RuntimeError("No supported printers found")
        if name_or_address:
            device = self._resolver.select_device(devices, name_or_address)
            if device is None:
                raise RuntimeError(f"No device matches '{name_or_address}'")
        else:
            device = devices[0]
        return device

    async def resolve_transport_target(
        self,
        name_or_address: Optional[str],
        transport: Optional[DeviceTransport] = None,
    ) -> ResolvedBluetoothTarget:
        """Scan Bluetooth and pick one raw target by name, address, or default."""
        if transport == DeviceTransport.CLASSIC:
            devices, _failures = await self._scan_endpoints(
                include_classic=True,
                include_ble=False,
            )
        elif transport == DeviceTransport.BLE:
            devices, _failures = await self._scan_endpoints(
                include_classic=False,
                include_ble=True,
            )
        else:
            devices, _failures = await self._scan_endpoints(
                include_classic=True,
                include_ble=True,
            )
        targets = self._transport_targets_from_scan(devices)
        if not targets:
            raise RuntimeError("No Bluetooth devices found")
        if name_or_address:
            target = self._resolver.select_transport_target(targets, name_or_address)
            if target is None:
                raise RuntimeError(f"No device matches '{name_or_address}'")
        else:
            target = targets[0]
        return target

    def _report_scan_debug(
        self,
        endpoints: Iterable[DeviceInfo],
        resolved: Iterable[PrinterDevice],
        failures: Iterable[ScanFailure],
    ) -> None:
        if self._reporter is None:
            return
        endpoint_list = list(endpoints)
        resolved_list = list(resolved)
        failure_list = list(failures)
        classic_seen = sum(1 for item in endpoint_list if item.transport == DeviceTransport.CLASSIC)
        ble_seen = sum(1 for item in endpoint_list if item.transport == DeviceTransport.BLE)
        merged = sum(1 for item in resolved_list if item.transport_badge == "[classic+ble]")
        attached_ble_addresses = {
            item.transport_target.ble_endpoint.address
            for item in resolved_list
            if isinstance(item.transport_target, BluetoothTarget)
            and item.transport_target.classic_endpoint is not None
            and item.transport_target.ble_endpoint is not None
        }
        self._reporter.debug(
            short="Discovery",
            detail=reporting.format_kv(
                "Discovery summary",
                classic_seen=classic_seen,
                ble_seen=ble_seen,
                merged=merged,
                supported=len(resolved_list),
                failures=len(failure_list),
            ),
        )
        for endpoint in endpoint_list:
            detected = self._catalog.detect_device(endpoint.name or "", endpoint.address)
            if detected is None:
                if endpoint.address in attached_ble_addresses:
                    self._reporter.debug(
                        short="Discovery",
                        detail=reporting.format_kv(
                            "Discovery attached",
                            name=endpoint.name or "<unknown>",
                            address=endpoint.address or "<unknown>",
                            transport=endpoint.transport.value,
                            reason="single_ble_endpoint_for_ble_first_profile",
                        ),
                    )
                    continue
                matches = self._catalog.detect_model(endpoint.name or "", endpoint.address)
                supported = [
                    match
                    for match in matches
                    if isinstance(match, SupportedModelMatch)
                ]
                unsupported = [
                    match
                    for match in matches
                    if isinstance(match, UnsupportedModelMatch)
                ]
                if len(supported) > 1:
                    reason = "ambiguous_supported_model"
                    candidates = ",".join(match.model.model_key for match in supported)
                elif unsupported:
                    reason = "known_unsupported_model"
                    candidates = ",".join(match.model.model_key for match in unsupported)
                else:
                    reason = "no_supported_model"
                    candidates = ""
                self._reporter.debug(
                    short="Discovery",
                    detail=reporting.format_kv(
                        "Discovery ignored",
                        name=endpoint.name or "<unknown>",
                        address=endpoint.address or "<unknown>",
                        transport=endpoint.transport.value,
                        reason=reason,
                        candidates=candidates,
                    ),
                )
                continue
            self._reporter.debug(
                short="Discovery",
                detail=reporting.format_kv(
                    "Discovery matched",
                    name=endpoint.name or "<unknown>",
                    address=endpoint.address or "<unknown>",
                    transport=endpoint.transport.value,
                    profile=detected.profile_key,
                    family=detected.protocol_family.value,
                    model=detected.model_key,
                ),
            )

    @staticmethod
    def _to_transport(endpoint: DeviceInfo) -> BluetoothEndpointTransport:
        if endpoint.transport == DeviceTransport.BLE:
            return BluetoothEndpointTransport.BLE
        return BluetoothEndpointTransport.CLASSIC

    @classmethod
    def _endpoint_from_scan(cls, endpoint: DeviceInfo) -> BluetoothEndpoint:
        return BluetoothEndpoint(
            name=endpoint.name or "",
            address=endpoint.address,
            paired=endpoint.paired,
            transport=cls._to_transport(endpoint),
        )

    @classmethod
    def _endpoints_from_scan(cls, devices: Iterable[DeviceInfo]) -> List[BluetoothEndpoint]:
        return [cls._endpoint_from_scan(endpoint) for endpoint in devices]

    def transport_target_from_endpoint(self, endpoint: DeviceInfo) -> BluetoothTarget:
        """Return a concrete Bluetooth target for one raw scan endpoint."""
        return self._resolver.transport_target_from_endpoint(self._endpoint_from_scan(endpoint))

__all__ = ["BluetoothDiscovery", "BluetoothScanResult"]
