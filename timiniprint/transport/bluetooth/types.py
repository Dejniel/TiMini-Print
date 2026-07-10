from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...devices.bluetooth_profiles import BleTransportProfile

SocketLike = Any


class DeviceTransport(str, Enum):
    CLASSIC = "classic"
    BLE = "ble"


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    address: str
    paired: Optional[bool] = None
    transport: DeviceTransport = DeviceTransport.CLASSIC
    ble_profile: BleTransportProfile | None = None
    ble_mtu_request: Optional[int] = None

    def __post_init__(self) -> None:
        if self.ble_mtu_request is not None and self.ble_mtu_request < 23:
            raise ValueError("ble_mtu_request must be at least 23")

    def merge(self, other: "DeviceInfo") -> "DeviceInfo":
        if self.address != other.address or self.transport != other.transport:
            raise ValueError("Cannot merge devices with different addresses or transports")
        if self.name and other.name:
            name = self.name if len(self.name) >= len(other.name) else other.name
        else:
            name = self.name or other.name
        if self.paired is True or other.paired is True:
            paired = True
        elif self.paired is False or other.paired is False:
            paired = False
        else:
            paired = None
        ble_profile = self.ble_profile or other.ble_profile
        ble_mtu_request = self.ble_mtu_request or other.ble_mtu_request
        return DeviceInfo(
            name=name,
            address=self.address,
            paired=paired,
            transport=self.transport,
            ble_profile=ble_profile,
            ble_mtu_request=ble_mtu_request,
        )

    @staticmethod
    def dedupe(devices: List["DeviceInfo"]) -> List["DeviceInfo"]:
        by_addr: Dict[Tuple[str, DeviceTransport], DeviceInfo] = {}
        for device in devices:
            key = (device.address, device.transport)
            existing = by_addr.get(key)
            if existing is None:
                by_addr[key] = device
            else:
                by_addr[key] = existing.merge(device)
        results = list(by_addr.values())
        results.sort(key=lambda item: (item.name or "", item.address, item.transport.value))
        return results


@dataclass(frozen=True)
class ScanFailure:
    transport: DeviceTransport
    error: Exception
