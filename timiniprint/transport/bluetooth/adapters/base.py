from __future__ import annotations

import asyncio
from typing import List, Optional, Set

from ..types import DeviceInfo, SocketLike


class _BluetoothAdapter:
    single_channel = False

    def scan_blocking(self, timeout: float) -> List[DeviceInfo]:
        raise NotImplementedError

    def create_socket(self) -> SocketLike:
        raise NotImplementedError

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        return None

    def ensure_paired(self, address: str) -> None:
        return None

    @staticmethod
    def _scan_bleak(timeout: float, paired_addresses: Optional[Set[str]] = None) -> List[DeviceInfo]:
        try:
            from bleak import BleakScanner
        except Exception:
            return []

        async def run() -> List[DeviceInfo]:
            found = await BleakScanner.discover(timeout=timeout)
            results = []
            for device in found:
                name = device.name or ""
                if paired_addresses is None:
                    paired = None
                else:
                    paired = device.address in paired_addresses
                results.append(DeviceInfo(name=name, address=device.address, paired=paired))
            return results

        try:
            devices = asyncio.run(run())
        except Exception:
            return []
        return devices
