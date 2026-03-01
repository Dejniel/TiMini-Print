from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from ..transport.bluetooth import DeviceInfo, SppBackend
from ..transport.bluetooth.types import DeviceTransport, ScanFailure
from .models import (
    PrinterModel,
    PrinterModelAliasNormalizer,
    PrinterModelMatch,
    PrinterModelMatchSource,
    PrinterModelRegistry,
)

_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


@dataclass(frozen=True)
class ResolvedBluetoothDevice:
    name: str
    model_match: PrinterModelMatch
    classic_endpoint: Optional[DeviceInfo]
    ble_endpoint: Optional[DeviceInfo]
    display_address: str
    transport_label: str

    @property
    def address(self) -> str:
        return self.display_address

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

    @property
    def model(self) -> PrinterModel:
        return self.model_match.model


@dataclass(frozen=True)
class _EndpointCandidate:
    device: DeviceInfo
    model_match: PrinterModelMatch
    normalized_name: str


class DeviceResolver:
    def __init__(self, registry: PrinterModelRegistry) -> None:
        self._registry = registry

    def filter_printer_devices(self, devices: Iterable[DeviceInfo]) -> List[DeviceInfo]:
        filtered = []
        for device in devices:
            if self._registry.detect_from_device_name(device.name or "", device.address):
                filtered.append(device)
        return filtered

    async def scan_printer_devices_with_failures(
        self,
        *,
        timeout: float = 5.0,
        include_classic: bool = True,
        include_ble: bool = True,
    ) -> Tuple[List[ResolvedBluetoothDevice], List[ScanFailure]]:
        devices, failures = await SppBackend.scan_with_failures(
            timeout=timeout,
            include_classic=include_classic,
            include_ble=include_ble,
        )
        return self.build_resolved_bluetooth_devices(devices), failures

    def build_resolved_bluetooth_devices(
        self,
        devices: Iterable[DeviceInfo],
    ) -> List[ResolvedBluetoothDevice]:
        filtered = self.filter_printer_devices(devices)
        candidates = self._build_endpoint_candidates(filtered)
        grouped = self._group_candidates(candidates)

        resolved: List[ResolvedBluetoothDevice] = []
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
        return self._sort_resolved_devices(resolved)

    async def resolve_printer_device(
        self,
        name_or_address: Optional[str],
        transport: Optional[DeviceTransport] = None,
    ) -> ResolvedBluetoothDevice:
        if transport == DeviceTransport.CLASSIC:
            devices, _ = await self.scan_printer_devices_with_failures(
                include_classic=True,
                include_ble=False,
            )
        elif transport == DeviceTransport.BLE:
            devices, _ = await self.scan_printer_devices_with_failures(
                include_classic=False,
                include_ble=True,
            )
        else:
            devices, _ = await self.scan_printer_devices_with_failures(
                include_classic=True,
                include_ble=True,
            )
        if not devices:
            raise RuntimeError("No supported printers found")
        if name_or_address:
            device = self._select_device(devices, name_or_address)
            if not device:
                raise RuntimeError(f"No device matches '{name_or_address}'")
            return device
        return devices[0]

    def resolve_model(
        self, device_name: str, model_no: Optional[str] = None, address: Optional[str] = None
    ) -> PrinterModel:
        match = self.resolve_model_with_origin(device_name, model_no, address)
        return match.model

    def resolve_model_with_origin(
        self, device_name: str, model_no: Optional[str] = None, address: Optional[str] = None
    ) -> PrinterModelMatch:
        if model_no:
            model = self._registry.get(model_no)
            if not model:
                raise RuntimeError(f"Unknown printer model '{model_no}'")
            return PrinterModelMatch(model=model, source=PrinterModelMatchSource.MODEL_NO)
        match = self._registry.detect_with_origin(device_name, address)
        if match:
            return match
        raise RuntimeError("Printer model not detected from Bluetooth name")

    def require_model(self, model_no: Optional[str]) -> PrinterModel:
        if not model_no:
            raise RuntimeError("Serial printing requires --model (see --list-models)")
        model = self._registry.get(model_no)
        if not model:
            raise RuntimeError(f"Unknown printer model '{model_no}'")
        return model

    def build_connection_attempts(self, resolved: ResolvedBluetoothDevice) -> List[DeviceInfo]:
        attempts: List[DeviceInfo] = []
        prefer_spp = resolved.model.use_spp
        ordered = [DeviceTransport.CLASSIC, DeviceTransport.BLE] if prefer_spp else [DeviceTransport.BLE, DeviceTransport.CLASSIC]
        for transport in ordered:
            endpoint = self._endpoint_for_transport(resolved, transport)
            if endpoint is not None:
                attempts.append(endpoint)
        return attempts

    @staticmethod
    def _endpoint_for_transport(
        resolved: ResolvedBluetoothDevice,
        transport: DeviceTransport,
    ) -> Optional[DeviceInfo]:
        if transport == DeviceTransport.CLASSIC:
            return resolved.classic_endpoint
        return resolved.ble_endpoint

    def _build_endpoint_candidates(self, devices: Iterable[DeviceInfo]) -> List[_EndpointCandidate]:
        candidates: List[_EndpointCandidate] = []
        for device in devices:
            try:
                match = self.resolve_model_with_origin(device.name or "", address=device.address)
            except Exception:
                continue
            normalized_name = PrinterModelAliasNormalizer.normalize_alias_name(device.name or "")
            candidates.append(
                _EndpointCandidate(
                    device=device,
                    model_match=match,
                    normalized_name=normalized_name,
                )
            )
        return candidates

    @staticmethod
    def _group_candidates(
        candidates: Iterable[_EndpointCandidate],
    ) -> Dict[Tuple[str, str], Dict[DeviceTransport, List[_EndpointCandidate]]]:
        grouped: Dict[Tuple[str, str], Dict[DeviceTransport, List[_EndpointCandidate]]] = {}
        for candidate in candidates:
            key = (candidate.model_match.model.model_no, candidate.normalized_name)
            bucket = grouped.setdefault(
                key,
                {DeviceTransport.CLASSIC: [], DeviceTransport.BLE: []},
            )
            bucket[candidate.device.transport].append(candidate)
        return grouped

    @staticmethod
    def _choose_name(primary: str, secondary: str) -> str:
        if primary and secondary:
            return primary if len(primary) >= len(secondary) else secondary
        return primary or secondary

    @staticmethod
    def _single_candidate(candidate: _EndpointCandidate) -> ResolvedBluetoothDevice:
        if candidate.device.transport == DeviceTransport.CLASSIC:
            classic_endpoint = candidate.device
            ble_endpoint = None
        else:
            classic_endpoint = None
            ble_endpoint = candidate.device
        display_address = classic_endpoint.address if classic_endpoint else ble_endpoint.address
        transport_label = "[classic]" if classic_endpoint else "[ble]"
        return ResolvedBluetoothDevice(
            name=candidate.device.name or "",
            model_match=candidate.model_match,
            classic_endpoint=classic_endpoint,
            ble_endpoint=ble_endpoint,
            display_address=display_address,
            transport_label=transport_label,
        )

    def _merge_candidates(
        self,
        classic_candidate: _EndpointCandidate,
        ble_candidate: _EndpointCandidate,
    ) -> ResolvedBluetoothDevice:
        name = self._choose_name(classic_candidate.device.name or "", ble_candidate.device.name or "")
        return ResolvedBluetoothDevice(
            name=name,
            model_match=classic_candidate.model_match,
            classic_endpoint=classic_candidate.device,
            ble_endpoint=ble_candidate.device,
            display_address=classic_candidate.device.address,
            transport_label="[classic+ble]",
        )

    @staticmethod
    def _looks_like_address(value: str) -> bool:
        trimmed = value.strip()
        return bool(_ADDRESS_RE.match(trimmed) or _UUID_RE.match(trimmed))

    @staticmethod
    def _sort_resolved_devices(devices: Iterable[ResolvedBluetoothDevice]) -> List[ResolvedBluetoothDevice]:
        return sorted(
            list(devices),
            key=lambda item: (item.name or "", item.display_address),
        )

    def _select_device(
        self,
        devices: Iterable[ResolvedBluetoothDevice],
        name_or_address: str,
    ) -> Optional[ResolvedBluetoothDevice]:
        if self._looks_like_address(name_or_address):
            target_address = name_or_address.lower()
            for device in devices:
                if device.display_address.lower() == target_address:
                    return device
                classic_endpoint = device.classic_endpoint
                if classic_endpoint and classic_endpoint.address.lower() == target_address:
                    return device
                ble_endpoint = device.ble_endpoint
                if ble_endpoint and ble_endpoint.address.lower() == target_address:
                    return device
            return None
        target = name_or_address.lower()
        for device in devices:
            if (device.name or "").strip().lower() == target:
                return device
        for device in devices:
            if target in (device.name or "").strip().lower():
                return device
        return None
