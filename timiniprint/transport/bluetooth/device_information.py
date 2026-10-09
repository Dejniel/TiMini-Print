"""Read-only, optional Bluetooth SIG Device Information (not printer identity)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic

from ... import reporting

DEVICE_INFORMATION_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
DEVICE_INFORMATION_CHARACTERISTICS = {
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
    "model": "00002a24-0000-1000-8000-00805f9b34fb",
    "firmware": "00002a26-0000-1000-8000-00805f9b34fb",
    "hardware": "00002a27-0000-1000-8000-00805f9b34fb",
    "software": "00002a28-0000-1000-8000-00805f9b34fb",
}
DEVICE_INFORMATION_TIMEOUT = 2.0


@dataclass(frozen=True)
class BleDeviceInformation:
    """Endpoint-tagged snapshot; missing fields are normal, read failures explicit.

    Values can identify the Bluetooth module rather than the printer. They must
    not select a printer protocol. Serial numbers are deliberately not collected.
    """

    address: str
    read_at: str
    service_present: bool
    values: dict[str, str]
    errors: dict[str, str]

    @classmethod
    def from_gatt(
        cls, address: str, *, service_present: bool,
        values: dict[str, bytes], errors: dict[str, str],
    ) -> BleDeviceInformation:
        """Decode the same raw characteristic results on every platform."""
        decoded = {}
        failures = {}
        for field, uuid in DEVICE_INFORMATION_CHARACTERISTICS.items():
            if uuid in errors:
                failures[field] = errors[uuid]
            elif uuid in values:
                try:
                    decoded[field] = values[uuid].decode("utf-8").rstrip("\x00")
                except UnicodeDecodeError:
                    failures[field] = "invalid_utf8"
        return cls(
            address=address,
            read_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            service_present=service_present, values=decoded, errors=failures,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "address": self.address, "transport": "ble", "read_at": self.read_at,
            "service_present": self.service_present,
            "values": dict(self.values), "errors": dict(self.errors),
        }

    def log(self, reporter: reporting.Reporter) -> None:
        reporter.debug(short="BLE device information", detail=json.dumps(self.as_dict()))


async def read_device_information(
    client, address: str, *, timeout: float = DEVICE_INFORMATION_TIMEOUT,
) -> BleDeviceInformation:
    """Read standard GATT strings on an already connected Bleak-compatible client.

    No writes, pairing, notification subscription or printer commands. One total
    deadline bounds optional reads. Cancellation remains cancellation, not a
    metadata failure. After a timed-out read do not queue more GATT requests.
    """
    services = getattr(client, "services", None) or ()
    service = next((item for item in services if str(item.uuid).lower() in (
        DEVICE_INFORMATION_SERVICE_UUID, "180a",
    )), None)
    values: dict[str, bytes] = {}
    errors: dict[str, str] = {}
    if service is not None:
        deadline = monotonic() + timeout
        for uuid in DEVICE_INFORMATION_CHARACTERISTICS.values():
            characteristic = next((item for item in service.characteristics
                                   if str(item.uuid).lower() in (uuid, uuid[4:8])), None)
            if characteristic is None:
                continue
            if "read" not in characteristic.properties:
                errors[uuid] = "not_readable"
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                errors[uuid] = "timeout"
                break
            try:
                values[uuid] = bytes(await asyncio.wait_for(
                    client.read_gatt_char(characteristic), timeout=remaining,
                ))
            except asyncio.TimeoutError:
                errors[uuid] = "timeout"
                break
            except Exception as exc:
                errors[uuid] = str(exc) or type(exc).__name__
    return BleDeviceInformation.from_gatt(
        address, service_present=service is not None, values=values, errors=errors,
    )


async def probe_ble_device_information(address: str) -> BleDeviceInformation:
    """Connect/read/disconnect without a printer profile or writable endpoint."""
    from bleak import BleakClient

    client = BleakClient(address, timeout=15.0)
    try:
        await client.connect()
        return await read_device_information(client, address)
    finally:
        await client.disconnect()
