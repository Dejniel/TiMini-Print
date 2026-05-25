from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from ... import reporting
from ...devices import BluetoothTarget, PrinterCatalog, PrinterDevice
from ...devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from ...devices.profiles import DetectionNormalizer
from .backend import SppBackend
from .types import DeviceInfo, DeviceTransport, ScanFailure

_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


@dataclass(frozen=True)
class _ResolvedEndpoint:
    endpoint: DeviceInfo
    device: PrinterDevice
    normalized_name: str


@dataclass(frozen=True)
class BluetoothScanResult:
    """Resolved Bluetooth scan output with logical devices and scan failures."""

    devices: List[PrinterDevice]
    failures: List[ScanFailure]
    raw_endpoints: List[DeviceInfo] = field(default_factory=list)


@dataclass(frozen=True)
class _ResolvedBluetoothTarget:
    """One raw Bluetooth target resolved without catalog model matching."""

    display_name: str
    transport_target: BluetoothTarget


class BluetoothDiscovery:
    """Discover reachable Bluetooth printers and resolve them into devices."""

    def __init__(
        self,
        catalog: PrinterCatalog,
        reporter: reporting.Reporter | None = None,
    ) -> None:
        self._catalog = catalog
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
            resolved_targets = self._transport_targets_from_scan(devices)
            needs_retry = any(
                item.transport_target.classic_endpoint is not None
                and item.transport_target.ble_endpoint is None
                for item in resolved_targets
            )
            if needs_retry:
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
            resolved_targets = self._transport_targets_from_scan(devices)
            needs_retry = any(
                item.transport_target.classic_endpoint is not None
                and item.transport_target.ble_endpoint is None
                for item in resolved_targets
            )
            if needs_retry:
                ble_devices, _failures = SppBackend.scan_with_failures_blocking(
                    timeout=timeout,
                    include_classic=False,
                    include_ble=True,
                )
                devices = DeviceInfo.dedupe(list(devices) + list(ble_devices))
        return devices, failures

    def _filter_supported_endpoints(self, devices: Iterable[DeviceInfo]) -> List[DeviceInfo]:
        filtered = []
        for device in devices:
            if self._catalog.detect_device(device.name or "", device.address) is not None:
                filtered.append(device)
        return filtered

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
        filtered = self._filter_supported_endpoints(devices)
        candidates = self._build_endpoint_candidates(filtered)
        grouped = self._group_candidates(candidates)

        resolved: List[PrinterDevice] = []
        for key in sorted(grouped.keys()):
            classic_items = grouped[key].get(DeviceTransport.CLASSIC, [])
            ble_items = grouped[key].get(DeviceTransport.BLE, [])
            if len(classic_items) == 1 and len(ble_items) == 1:
                resolved.append(self._merge_candidates(classic_items[0], ble_items[0]))
                continue
            for item in classic_items:
                resolved.append(self._single_candidate(item))
            for item in ble_items:
                resolved.append(self._single_candidate(item))
        return self._sort_devices(resolved)

    def _transport_targets_from_scan(
        self,
        devices: Iterable[DeviceInfo],
    ) -> List[_ResolvedBluetoothTarget]:
        """Resolve raw scan endpoints into logical Bluetooth transport targets."""
        grouped: Dict[str, Dict[DeviceTransport, List[DeviceInfo]]] = {}
        for endpoint in devices:
            normalized_name = DetectionNormalizer.fold_name(endpoint.name or "")
            key = normalized_name or endpoint.address.lower()
            bucket = grouped.setdefault(
                key,
                {DeviceTransport.CLASSIC: [], DeviceTransport.BLE: []},
            )
            bucket[endpoint.transport].append(endpoint)

        resolved: List[_ResolvedBluetoothTarget] = []
        for key in sorted(grouped.keys()):
            classic_items = grouped[key].get(DeviceTransport.CLASSIC, [])
            ble_items = grouped[key].get(DeviceTransport.BLE, [])
            if len(classic_items) == 1 and len(ble_items) == 1:
                resolved.append(self._merge_transport_targets(classic_items[0], ble_items[0]))
                continue
            for item in classic_items:
                resolved.append(self._single_transport_target(item))
            for item in ble_items:
                resolved.append(self._single_transport_target(item))
        return self._sort_transport_targets(resolved)

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
            device = self._select_device(devices, name_or_address)
            if device is None:
                raise RuntimeError(f"No device matches '{name_or_address}'")
        else:
            device = devices[0]
        return device

    async def resolve_transport_target(
        self,
        name_or_address: Optional[str],
        transport: Optional[DeviceTransport] = None,
    ) -> _ResolvedBluetoothTarget:
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
            target = self._select_transport_target(targets, name_or_address)
            if target is None:
                raise RuntimeError(f"No device matches '{name_or_address}'")
        else:
            target = targets[0]
        return target

    def _build_endpoint_candidates(self, devices: Iterable[DeviceInfo]) -> List[_ResolvedEndpoint]:
        candidates: List[_ResolvedEndpoint] = []
        for endpoint in devices:
            device = self._catalog.detect_device(endpoint.name or "", endpoint.address)
            if device is None:
                continue
            candidates.append(
                _ResolvedEndpoint(
                    endpoint=endpoint,
                    device=device,
                    normalized_name=DetectionNormalizer.fold_name(endpoint.name or ""),
                )
            )
        return candidates

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
                self._reporter.debug(
                    short="Discovery",
                    detail=reporting.format_kv(
                        "Discovery ignored",
                        name=endpoint.name or "<unknown>",
                        address=endpoint.address or "<unknown>",
                        transport=endpoint.transport.value,
                        reason="no_supported_rule",
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
                    rule=detected.detection_rule_key,
                ),
            )

    @staticmethod
    def _group_candidates(
        candidates: Iterable[_ResolvedEndpoint],
    ) -> Dict[Tuple[str, str], Dict[DeviceTransport, List[_ResolvedEndpoint]]]:
        grouped: Dict[Tuple[str, str], Dict[DeviceTransport, List[_ResolvedEndpoint]]] = {}
        for candidate in candidates:
            # TODO: This classic+BLE merge is intentionally name/profile-based
            # for the single-printer workflow. Revisit only if endpoint pairing
            # ambiguity starts causing connection regressions in practice.
            key = (candidate.device.profile_key, candidate.normalized_name)
            bucket = grouped.setdefault(
                key,
                {DeviceTransport.CLASSIC: [], DeviceTransport.BLE: []},
            )
            bucket[candidate.endpoint.transport].append(candidate)
        return grouped

    @staticmethod
    def _choose_name(primary: str, secondary: str) -> str:
        if primary and secondary:
            return primary if len(primary) >= len(secondary) else secondary
        return primary or secondary

    @staticmethod
    def _to_transport(endpoint: DeviceInfo) -> BluetoothEndpointTransport:
        if endpoint.transport == DeviceTransport.BLE:
            return BluetoothEndpointTransport.BLE
        return BluetoothEndpointTransport.CLASSIC

    def _single_candidate(self, candidate: _ResolvedEndpoint) -> PrinterDevice:
        return candidate.device.with_transport_target(
            self._single_transport_target(candidate.endpoint).transport_target
        )

    def _merge_candidates(
        self,
        classic_candidate: _ResolvedEndpoint,
        ble_candidate: _ResolvedEndpoint,
    ) -> PrinterDevice:
        target = BluetoothTarget(
            classic_endpoint=BluetoothEndpoint(
                name=classic_candidate.endpoint.name or "",
                address=classic_candidate.endpoint.address,
                paired=classic_candidate.endpoint.paired,
                transport=BluetoothEndpointTransport.CLASSIC,
            ),
            ble_endpoint=BluetoothEndpoint(
                name=ble_candidate.endpoint.name or "",
                address=ble_candidate.endpoint.address,
                paired=ble_candidate.endpoint.paired,
                transport=BluetoothEndpointTransport.BLE,
            ),
            display_address=classic_candidate.endpoint.address,
            transport_badge="[classic+ble]",
        )
        merged_name = self._choose_name(
            classic_candidate.device.display_name,
            ble_candidate.device.display_name,
        )
        return PrinterDevice(
            display_name=merged_name,
            profile=classic_candidate.device.profile,
            protocol_family=classic_candidate.device.protocol_family,
            protocol_variant=classic_candidate.device.protocol_variant,
            image_pipeline=classic_candidate.device.image_pipeline,
            runtime_variant=classic_candidate.device.runtime_variant,
            runtime_density_profile=classic_candidate.device.runtime_density_profile,
            transport_target=target,
            detection_rule_key=classic_candidate.device.detection_rule_key,
        )

    def _single_transport_target(self, endpoint: DeviceInfo) -> _ResolvedBluetoothTarget:
        target = self.transport_target_from_endpoint(endpoint)
        return _ResolvedBluetoothTarget(
            display_name=endpoint.name or endpoint.address,
            transport_target=target,
        )

    @staticmethod
    def transport_target_from_endpoint(endpoint: DeviceInfo) -> BluetoothTarget:
        """Return a concrete Bluetooth target for one raw scan endpoint."""
        bluetooth_endpoint = BluetoothEndpoint(
            name=endpoint.name or "",
            address=endpoint.address,
            paired=endpoint.paired,
            transport=BluetoothDiscovery._to_transport(endpoint),
        )
        if bluetooth_endpoint.transport is BluetoothEndpointTransport.CLASSIC:
            return BluetoothTarget(
                classic_endpoint=bluetooth_endpoint,
                ble_endpoint=None,
                display_address=bluetooth_endpoint.address,
                transport_badge="[classic]",
            )
        return BluetoothTarget(
            classic_endpoint=None,
            ble_endpoint=bluetooth_endpoint,
            display_address=bluetooth_endpoint.address,
            transport_badge="[ble]",
        )

    def _merge_transport_targets(
        self,
        classic_endpoint: DeviceInfo,
        ble_endpoint: DeviceInfo,
    ) -> _ResolvedBluetoothTarget:
        target = BluetoothTarget(
            classic_endpoint=BluetoothEndpoint(
                name=classic_endpoint.name or "",
                address=classic_endpoint.address,
                paired=classic_endpoint.paired,
                transport=BluetoothEndpointTransport.CLASSIC,
            ),
            ble_endpoint=BluetoothEndpoint(
                name=ble_endpoint.name or "",
                address=ble_endpoint.address,
                paired=ble_endpoint.paired,
                transport=BluetoothEndpointTransport.BLE,
            ),
            display_address=classic_endpoint.address,
            transport_badge="[classic+ble]",
        )
        return _ResolvedBluetoothTarget(
            display_name=self._choose_name(
                classic_endpoint.name or "",
                ble_endpoint.name or "",
            )
            or classic_endpoint.address
            or ble_endpoint.address,
            transport_target=target,
        )

    @staticmethod
    def _looks_like_address(value: str) -> bool:
        trimmed = value.strip()
        return bool(_ADDRESS_RE.match(trimmed) or _UUID_RE.match(trimmed))

    @staticmethod
    def _sort_devices(devices: Iterable[PrinterDevice]) -> List[PrinterDevice]:
        return sorted(list(devices), key=lambda item: (item.display_name or "", item.address))

    @staticmethod
    def _sort_transport_targets(
        targets: Iterable[_ResolvedBluetoothTarget],
    ) -> List[_ResolvedBluetoothTarget]:
        return sorted(
            list(targets),
            key=lambda item: (
                item.display_name or "",
                item.transport_target.display_address,
            ),
        )

    def _select_device(
        self,
        devices: Iterable[PrinterDevice],
        name_or_address: str,
    ) -> Optional[PrinterDevice]:
        if self._looks_like_address(name_or_address):
            target_address = name_or_address.lower()
            for device in devices:
                if device.address.lower() == target_address:
                    return device
                target = device.transport_target
                if not isinstance(target, BluetoothTarget):
                    continue
                if target.classic_endpoint and target.classic_endpoint.address.lower() == target_address:
                    return device
                if target.ble_endpoint and target.ble_endpoint.address.lower() == target_address:
                    return device
            return None
        target = name_or_address.lower()
        for device in devices:
            if (device.display_name or "").strip().lower() == target:
                return device
        for device in devices:
            if target in (device.display_name or "").strip().lower():
                return device
        return None

    def _select_transport_target(
        self,
        targets: Iterable[_ResolvedBluetoothTarget],
        name_or_address: str,
    ) -> Optional[_ResolvedBluetoothTarget]:
        if self._looks_like_address(name_or_address):
            target_address = name_or_address.lower()
            for item in targets:
                target = item.transport_target
                if target.display_address.lower() == target_address:
                    return item
                if target.classic_endpoint and target.classic_endpoint.address.lower() == target_address:
                    return item
                if target.ble_endpoint and target.ble_endpoint.address.lower() == target_address:
                    return item
            return None
        target_name = name_or_address.lower()
        for item in targets:
            if (item.display_name or "").strip().lower() == target_name:
                return item
        for item in targets:
            if target_name in (item.display_name or "").strip().lower():
                return item
        return None


__all__ = ["BluetoothDiscovery", "BluetoothScanResult"]
