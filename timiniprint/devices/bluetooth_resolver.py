from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Tuple

from .catalog import PrinterCatalog
from .device import (
    BluetoothEndpoint,
    BluetoothEndpointTransport,
    BluetoothTarget,
    PrinterDevice,
    TransportTarget,
)
from .profiles import DetectionNormalizer, ModelMatch, WhitespaceMode

_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


@dataclass(frozen=True)
class ResolvedBluetoothTarget:
    """One Bluetooth target grouped without choosing a printer model.

    ``display_name`` is presentation only and may fall back to an address.
    Catalog detection uses the original names stored on the endpoints.
    """

    display_name: str
    transport_target: BluetoothTarget


class BluetoothEndpointResolver:
    """Resolve raw Bluetooth endpoints into logical printer devices."""

    def __init__(self, catalog: PrinterCatalog) -> None:
        self._catalog = catalog

    def devices_from_endpoints(self, endpoints: Iterable[BluetoothEndpoint]) -> List[PrinterDevice]:
        """Return only targets with one unambiguous catalog model."""
        choices = self.devices_for_display(endpoints)
        models_by_target: Dict[TransportTarget | None, set[str]] = {}
        for device in choices:
            models_by_target.setdefault(device.transport_target, set()).add(device.model_key)
        return [device for device in choices if len(models_by_target[device.transport_target]) == 1]

    def devices_for_display(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[PrinterDevice]:
        """Return all catalog choices on the same targets as automatic detection."""
        devices: List[PrinterDevice] = []
        unknown_targets: List[ResolvedBluetoothTarget] = []
        for target in self.transport_targets_from_endpoints(endpoints):
            candidates = self._detection_devices_for_target(target)
            devices.extend(candidates)
            if not candidates:
                unknown_targets.append(target)
        return self.sort_devices(self._with_anonymous_ble_candidate(devices, unknown_targets))

    def manual_targets_for_display(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[ResolvedBluetoothTarget]:
        """Return targets with no catalog choice, including unmatched anonymous BLE."""
        return [
            target for target in self.transport_targets_from_endpoints(endpoints)
            if not self._detection_devices_for_target(target)
        ]

    @staticmethod
    def _with_anonymous_ble_candidate(
        devices: List[PrinterDevice],
        unknown_targets: List[ResolvedBluetoothTarget],
    ) -> List[PrinterDevice]:
        """Keep the single-printer BLE-first heuristic separate from grouping."""
        if len(devices) != 1:
            return devices
        device = devices[0]
        target = device.transport_target
        if (device.profile.use_spp or not isinstance(target, BluetoothTarget)
                or target.classic_endpoint is None or target.ble_endpoint is not None):
            return devices
        anonymous = [item.transport_target for item in unknown_targets
                     if item.transport_target.ble_endpoint is not None
                     and not (item.transport_target.ble_endpoint.name or "").strip()]
        if len(anonymous) != 1 or anonymous[0].classic_endpoint is not None:
            return devices
        return [device.with_transport_target(replace(target, ble_candidate=anonymous[0].ble_endpoint))]

    def _detection_devices_for_target(self, item: ResolvedBluetoothTarget) -> List[PrinterDevice]:
        """Match endpoint identities, never the target's display label."""
        target = item.transport_target
        matches: Dict[str, ModelMatch] = {}
        for endpoint in (target.classic_endpoint, target.ble_endpoint):
            if endpoint is None:
                continue
            for match in self._catalog.detect_model(
                endpoint.name or "", endpoint.address, manufacturer_data=endpoint.manufacturer_data,
            ):
                previous = matches.get(match.model.model_key)
                if previous is not None:
                    match = replace(match, origin_ids=tuple(
                        origin for origin in match.model.origin_ids
                        if origin in previous.origin_ids or origin in match.origin_ids
                    ))
                matches[match.model.model_key] = match
        return [
            self._catalog.device_from_match(
                match, display_name=item.display_name, transport_target=target,
            )
            for match in matches.values()
        ]

    def transport_targets_from_endpoints(
        self,
        endpoints: Iterable[BluetoothEndpoint],
    ) -> List[ResolvedBluetoothTarget]:
        """Pair unique MACs, then catalog-compatible peers within name groups.

        A MAC pair must not make remaining name matches newly unambiguous.
        Keep the original endpoint metadata; grouping does not select a model.
        """
        grouped: Dict[Tuple[str, str], Dict[BluetoothEndpointTransport, List[BluetoothEndpoint]]] = {}
        by_mac: Dict[str, List[BluetoothEndpoint]] = {}
        models_by_endpoint: Dict[BluetoothEndpoint, set[str]] = {}
        for endpoint in self._share_advertisement_data(endpoints):
            matches = self._catalog.detect_model(
                endpoint.name or "", endpoint.address, manufacturer_data=endpoint.manufacturer_data,
            )
            models_by_endpoint[endpoint] = {match.model.model_key for match in matches}
            if DetectionNormalizer.is_mac_like_address(endpoint.address):
                mac = DetectionNormalizer.normalize_mac_candidate(endpoint.address)
                by_mac.setdefault(mac, []).append(endpoint)
            normalized_name = self._raw_endpoint_group_name(endpoint, matches)
            key = ("name", normalized_name) if normalized_name else ("address", endpoint.address.lower())
            bucket = grouped.setdefault(
                key,
                {BluetoothEndpointTransport.CLASSIC: [], BluetoothEndpointTransport.BLE: []},
            )
            bucket[endpoint.transport].append(endpoint)

        resolved: List[ResolvedBluetoothTarget] = []
        paired: set[BluetoothEndpoint] = set()
        for mac in sorted(by_mac):
            peers = by_mac[mac]
            if len(peers) != 2:
                continue
            classic = next((item for item in peers if item.transport == BluetoothEndpointTransport.CLASSIC), None)
            ble = next((item for item in peers if item.transport == BluetoothEndpointTransport.BLE), None)
            if classic is None or ble is None:
                continue
            resolved.append(self._merge_transport_targets(classic, ble))
            paired.update((classic, ble))
        for key in sorted(grouped.keys()):
            classic_items = grouped[key].get(BluetoothEndpointTransport.CLASSIC, [])
            ble_items = grouped[key].get(BluetoothEndpointTransport.BLE, [])
            if (len(classic_items) == 1 and len(ble_items) == 1
                    and classic_items[0] not in paired
                    and ble_items[0] not in paired):
                resolved.append(self._merge_transport_targets(classic_items[0], ble_items[0]))
                paired.update((classic_items[0], ble_items[0]))
                continue
            # Count partners in the original group, including MAC-paired peers.
            # Consuming an endpoint must not manufacture a unique model pair.
            for classic in classic_items:
                if classic in paired:
                    continue
                partners = [ble for ble in ble_items
                            if models_by_endpoint[classic] & models_by_endpoint[ble]]
                if len(partners) != 1 or partners[0] in paired:
                    continue
                ble = partners[0]
                if sum(bool(models_by_endpoint[other] & models_by_endpoint[ble])
                       for other in classic_items) != 1:
                    continue
                resolved.append(self._merge_transport_targets(classic, ble))
                paired.update((classic, ble))
            for item in (*classic_items, *ble_items):
                if item not in paired:
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
        named_devices = [(device, self._selection_names(device.display_name, device.transport_target))
                         for device in devices]
        for device, names in named_devices:
            if target in names:
                return device
        for device, names in named_devices:
            if any(target in name for name in names):
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
        named_targets = [(item, self._selection_names(item.display_name, item.transport_target))
                         for item in targets]
        for item, names in named_targets:
            if target_name in names:
                return item
        for item, names in named_targets:
            if any(target_name in name for name in names):
                return item
        return None

    @staticmethod
    def _selection_names(display_name: str, target: TransportTarget | None) -> tuple[str, ...]:
        """Selection aliases only; never use display labels for catalog matching."""
        names = [display_name]
        if isinstance(target, BluetoothTarget):
            names.extend(endpoint.name for endpoint in (target.classic_endpoint, target.ble_endpoint)
                         if endpoint is not None)
        return tuple(name.strip().lower() for name in names if name and name.strip())

    @staticmethod
    def _share_advertisement_data(endpoints: Iterable[BluetoothEndpoint]) -> List[BluetoothEndpoint]:
        endpoints = list(endpoints)
        advertised = {
            DetectionNormalizer.normalize_mac_candidate(endpoint.address): endpoint.manufacturer_data
            for endpoint in endpoints
            if endpoint.transport is BluetoothEndpointTransport.BLE
            and DetectionNormalizer.is_mac_like_address(endpoint.address)
            and endpoint.manufacturer_data
        }
        # Advertisement evidence describes the physical device, including its
        # Classic endpoint at the same MAC. Never share it merely by name.
        return [
            replace(endpoint, manufacturer_data=advertised.get(
                DetectionNormalizer.normalize_mac_candidate(endpoint.address), (),
            )) if not endpoint.manufacturer_data and
            DetectionNormalizer.normalize_mac_candidate(endpoint.address) in advertised else endpoint
            for endpoint in endpoints
        ]

    @staticmethod
    def _raw_endpoint_group_name(endpoint: BluetoothEndpoint, matches: tuple[ModelMatch, ...]) -> str:
        name = endpoint.name or ""
        matched_modes = {match.model.whitespace_mode for match in matches}
        whitespace_mode = (
            matched_modes.pop()
            if len(matched_modes) == 1
            else WhitespaceMode.PRESERVE
        )
        return DetectionNormalizer.fold_name(name, whitespace_mode)

    @staticmethod
    def _choose_name(primary: str, secondary: str) -> str:
        if primary and secondary:
            return primary if len(primary) >= len(secondary) else secondary
        return primary or secondary

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
