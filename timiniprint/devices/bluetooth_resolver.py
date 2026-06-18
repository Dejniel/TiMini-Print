from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .catalog import PrinterCatalog
from .device import (
    BluetoothEndpoint,
    BluetoothEndpointTransport,
    BluetoothTarget,
    PrinterDevice,
)
from .profiles import DetectionNormalizer

_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


@dataclass(frozen=True)
class ResolvedBluetoothTarget:
    """One raw Bluetooth target resolved without catalog model matching."""

    display_name: str
    transport_target: BluetoothTarget


@dataclass(frozen=True)
class _ResolvedEndpoint:
    endpoint: BluetoothEndpoint
    device: PrinterDevice
    normalized_name: str


class BluetoothEndpointResolver:
    """Resolve raw Bluetooth endpoints into logical printer devices."""

    def __init__(self, catalog: PrinterCatalog) -> None:
        self._catalog = catalog

    def devices_from_endpoints(self, endpoints: Iterable[BluetoothEndpoint]) -> List[PrinterDevice]:
        """Resolve raw scan endpoints into logical printer devices."""
        endpoint_list = list(endpoints)
        candidates = self._build_endpoint_candidates(endpoint_list)
        grouped = self._group_candidates(candidates)

        resolved: List[PrinterDevice] = []
        for key in sorted(grouped.keys()):
            classic_items = grouped[key].get(BluetoothEndpointTransport.CLASSIC, [])
            ble_items = grouped[key].get(BluetoothEndpointTransport.BLE, [])
            if len(classic_items) == 1 and len(ble_items) == 1:
                resolved.append(self._merge_candidates(classic_items[0], ble_items[0]))
                continue
            for item in classic_items:
                resolved.append(self._single_candidate(item))
            for item in ble_items:
                resolved.append(self._single_candidate(item))
        resolved = self._attach_single_anonymous_ble_endpoint(resolved, endpoint_list)
        return self.sort_devices(resolved)

    def devices_for_display(
        self,
        resolved_devices: Iterable[PrinterDevice],
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[PrinterDevice]:
        """Return resolved devices plus manual candidates for ambiguous names."""
        devices = list(resolved_devices)
        seen = {
            (device.model_key, device.address, device.transport_badge)
            for device in devices
        }
        for endpoint in endpoints:
            candidates = self._candidate_devices_from_endpoint(endpoint)
            if len(candidates) <= 1:
                continue
            for candidate in candidates:
                key = (candidate.model_key, candidate.address, candidate.transport_badge)
                if key in seen:
                    continue
                seen.add(key)
                devices.append(candidate)
        return self.sort_devices(devices)

    def manual_targets_for_display(
        self,
        resolved_devices: Iterable[PrinterDevice],
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[ResolvedBluetoothTarget]:
        """Return raw targets that need a manual model choice before printing."""
        represented = self._represented_endpoint_keys(resolved_devices)
        manual_targets: List[ResolvedBluetoothTarget] = []
        for item in self.transport_targets_from_endpoints(endpoints):
            target_keys = self._target_endpoint_keys(item.transport_target)
            if target_keys and target_keys.issubset(represented):
                continue
            candidates = self._catalog.detection_devices(
                item.display_name,
                item.transport_target.display_address,
                display_name=item.display_name,
                transport_target=item.transport_target,
            )
            if candidates:
                continue
            manual_targets.append(item)
        return self.sort_transport_targets(manual_targets)

    def transport_targets_from_endpoints(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[ResolvedBluetoothTarget]:
        """Resolve raw scan endpoints into logical Bluetooth transport targets."""
        grouped: Dict[str, Dict[BluetoothEndpointTransport, List[BluetoothEndpoint]]] = {}
        for endpoint in endpoints:
            normalized_name = DetectionNormalizer.fold_name(endpoint.name or "")
            key = normalized_name or endpoint.address.lower()
            bucket = grouped.setdefault(
                key,
                {BluetoothEndpointTransport.CLASSIC: [], BluetoothEndpointTransport.BLE: []},
            )
            bucket[endpoint.transport].append(endpoint)

        resolved: List[ResolvedBluetoothTarget] = []
        for key in sorted(grouped.keys()):
            classic_items = grouped[key].get(BluetoothEndpointTransport.CLASSIC, [])
            ble_items = grouped[key].get(BluetoothEndpointTransport.BLE, [])
            if len(classic_items) == 1 and len(ble_items) == 1:
                resolved.append(self._merge_transport_targets(classic_items[0], ble_items[0]))
                continue
            for item in classic_items:
                resolved.append(self._single_transport_target(item))
            for item in ble_items:
                resolved.append(self._single_transport_target(item))
        return self.sort_transport_targets(resolved)

    def transport_target_from_endpoint(self, endpoint: BluetoothEndpoint) -> BluetoothTarget:
        """Return a concrete Bluetooth target for one raw scan endpoint."""
        if endpoint.transport is BluetoothEndpointTransport.CLASSIC:
            return BluetoothTarget(
                classic_endpoint=endpoint,
                ble_endpoint=None,
                display_address=endpoint.address,
                transport_badge="[classic]",
            )
        return BluetoothTarget(
            classic_endpoint=None,
            ble_endpoint=endpoint,
            display_address=endpoint.address,
            transport_badge="[ble]",
        )

    def select_device(
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

    def select_transport_target(
        self,
        targets: Iterable[ResolvedBluetoothTarget],
        name_or_address: str,
    ) -> Optional[ResolvedBluetoothTarget]:
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

    def _build_endpoint_candidates(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[_ResolvedEndpoint]:
        candidates: List[_ResolvedEndpoint] = []
        for endpoint in endpoints:
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

    def _candidate_devices_from_endpoint(
        self,
        endpoint: BluetoothEndpoint,
    ) -> List[PrinterDevice]:
        return list(
            self._catalog.detection_devices(
                endpoint.name or "",
                endpoint.address,
                display_name=endpoint.name or "",
                transport_target=self.transport_target_from_endpoint(endpoint),
            )
        )

    @staticmethod
    def _group_candidates(
        candidates: Iterable[_ResolvedEndpoint],
    ) -> Dict[Tuple[str, str, str], Dict[BluetoothEndpointTransport, List[_ResolvedEndpoint]]]:
        grouped: Dict[Tuple[str, str, str], Dict[BluetoothEndpointTransport, List[_ResolvedEndpoint]]] = {}
        for candidate in candidates:
            # This classic+BLE merge is intentionally name/profile-based for the
            # single-printer workflow. Revisit only if endpoint pairing ambiguity
            # starts causing connection regressions in practice.
            key = (
                candidate.device.model_key,
                candidate.device.profile_key,
                candidate.normalized_name,
            )
            bucket = grouped.setdefault(
                key,
                {BluetoothEndpointTransport.CLASSIC: [], BluetoothEndpointTransport.BLE: []},
            )
            bucket[candidate.endpoint.transport].append(candidate)
        return grouped

    @staticmethod
    def _choose_name(primary: str, secondary: str) -> str:
        if primary and secondary:
            return primary if len(primary) >= len(secondary) else secondary
        return primary or secondary

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
            classic_endpoint=classic_candidate.endpoint,
            ble_endpoint=ble_candidate.endpoint,
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
            runtime_settings=classic_candidate.device.runtime_settings,
            transport_target=target,
            model_key=classic_candidate.device.model_key,
            origin_app_packages=classic_candidate.device.origin_app_packages,
        )

    def _attach_single_anonymous_ble_endpoint(
        self,
        resolved: List[PrinterDevice],
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[PrinterDevice]:
        if len(resolved) != 1:
            return resolved
        device = resolved[0]
        if device.profile.use_spp:
            return resolved
        target = device.transport_target
        if (
            not isinstance(target, BluetoothTarget)
            or target.classic_endpoint is None
            or target.ble_endpoint is not None
        ):
            return resolved

        anonymous_ble = [
            endpoint
            for endpoint in endpoints
            if endpoint.transport == BluetoothEndpointTransport.BLE
            and not (endpoint.name or "").strip()
            and self._catalog.detect_device(endpoint.name or "", endpoint.address) is None
        ]
        if len(anonymous_ble) != 1:
            return resolved

        return [
            device.with_transport_target(
                BluetoothTarget(
                    classic_endpoint=target.classic_endpoint,
                    ble_endpoint=anonymous_ble[0],
                    display_address=target.display_address,
                    transport_badge="[classic+ble]",
                )
            )
        ]

    def _single_transport_target(self, endpoint: BluetoothEndpoint) -> ResolvedBluetoothTarget:
        return ResolvedBluetoothTarget(
            display_name=endpoint.name or endpoint.address,
            transport_target=self.transport_target_from_endpoint(endpoint),
        )

    def _merge_transport_targets(
        self,
        classic_endpoint: BluetoothEndpoint,
        ble_endpoint: BluetoothEndpoint,
    ) -> ResolvedBluetoothTarget:
        target = BluetoothTarget(
            classic_endpoint=classic_endpoint,
            ble_endpoint=ble_endpoint,
            display_address=classic_endpoint.address,
            transport_badge="[classic+ble]",
        )
        return ResolvedBluetoothTarget(
            display_name=self._choose_name(
                classic_endpoint.name or "",
                ble_endpoint.name or "",
            )
            or classic_endpoint.address
            or ble_endpoint.address,
            transport_target=target,
        )

    @staticmethod
    def _endpoint_key(endpoint: BluetoothEndpoint) -> Tuple[BluetoothEndpointTransport, str]:
        return (endpoint.transport, endpoint.address.lower())

    @classmethod
    def _target_endpoint_keys(cls, target: BluetoothTarget) -> set[Tuple[BluetoothEndpointTransport, str]]:
        keys = set()
        if target.classic_endpoint is not None:
            keys.add(cls._endpoint_key(target.classic_endpoint))
        if target.ble_endpoint is not None:
            keys.add(cls._endpoint_key(target.ble_endpoint))
        return keys

    @classmethod
    def _represented_endpoint_keys(
        cls,
        devices: Iterable[PrinterDevice],
    ) -> set[Tuple[BluetoothEndpointTransport, str]]:
        keys = set()
        for device in devices:
            target = device.transport_target
            if isinstance(target, BluetoothTarget):
                keys.update(cls._target_endpoint_keys(target))
        return keys

    @staticmethod
    def _looks_like_address(value: str) -> bool:
        trimmed = value.strip()
        return bool(_ADDRESS_RE.match(trimmed) or _UUID_RE.match(trimmed))

    @staticmethod
    def sort_devices(devices: Iterable[PrinterDevice]) -> List[PrinterDevice]:
        return sorted(list(devices), key=lambda item: (item.display_name or "", item.address))

    @staticmethod
    def sort_transport_targets(
        targets: Iterable[ResolvedBluetoothTarget],
    ) -> List[ResolvedBluetoothTarget]:
        return sorted(
            list(targets),
            key=lambda item: (
                item.display_name or "",
                item.transport_target.display_address,
            ),
        )
