from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

SocketLike = Any


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    address: str
    paired: Optional[bool] = None

    def merge(self, other: "DeviceInfo") -> "DeviceInfo":
        if self.address != other.address:
            raise ValueError("Cannot merge devices with different addresses")
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
        return DeviceInfo(name=name, address=self.address, paired=paired)

    @staticmethod
    def dedupe(devices: List["DeviceInfo"]) -> List["DeviceInfo"]:
        by_addr: Dict[str, DeviceInfo] = {}
        for device in devices:
            existing = by_addr.get(device.address)
            if existing is None:
                by_addr[device.address] = device
            else:
                by_addr[device.address] = existing.merge(device)
        results = list(by_addr.values())
        results.sort(key=lambda item: (item.name or "", item.address))
        return results
