from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

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


class BluetoothDiscovery:
    """Discover reachable Bluetooth printers and resolve them into devices."""

    def __init__(self, catalog: PrinterCatalog) -> None:
        self._catalog = catalog

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
        devices, failures = await SppBackend.scan_with_failures(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        if include_classic and include_ble:
            resolved = self.devices_from_scan(devices)
            needs_retry = any(
                isinstance(item.transport_target, BluetoothTarget)
                and item.transport_target.classic_endpoint is not None
                and item.transport_target.ble_endpoint is None
                for item in resolved
            )
            if needs_retry:
                ble_devices, _failures = await SppBackend.scan_with_failures(
                    timeout=timeout,
                    include_classic=False,
                    include_ble=True,
                )
                devices = DeviceInfo.dedupe(list(devices) + list(ble_devices))
        return BluetoothScanResult(
            devices=self.devices_from_scan(devices),
            failures=failures,
        )

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
        endpoint = BluetoothEndpoint(
            name=candidate.endpoint.name or "",
            address=candidate.endpoint.address,
            paired=candidate.endpoint.paired,
            transport=self._to_transport(candidate.endpoint),
        )
        if endpoint.transport is BluetoothEndpointTransport.CLASSIC:
            target = BluetoothTarget(
                classic_endpoint=endpoint,
                ble_endpoint=None,
                display_address=endpoint.address,
                transport_badge="[classic]",
            )
        else:
            target = BluetoothTarget(
                classic_endpoint=None,
                ble_endpoint=endpoint,
                display_address=endpoint.address,
                transport_badge="[ble]",
            )
        return candidate.device.with_transport_target(target)

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
            testing=classic_candidate.device.testing,
            testing_note=classic_candidate.device.testing_note,
        )

    @staticmethod
    def _looks_like_address(value: str) -> bool:
        trimmed = value.strip()
        return bool(_ADDRESS_RE.match(trimmed) or _UUID_RE.match(trimmed))

    @staticmethod
    def _sort_devices(devices: Iterable[PrinterDevice]) -> List[PrinterDevice]:
        return sorted(list(devices), key=lambda item: (item.display_name or "", item.address))

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


__all__ = ["BluetoothDiscovery"]
