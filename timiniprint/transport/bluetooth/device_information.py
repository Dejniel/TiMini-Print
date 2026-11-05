"""Read-only, optional Bluetooth SIG Device Information (not printer identity)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from ... import reporting
from .bleak_lifecycle import _BleakLifecycle

DEVICE_INFORMATION_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
_GAP_SERVICE_UUID = "00001800-0000-1000-8000-00805f9b34fb"


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8").rstrip("\x00")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_utf8") from exc


def _pnp_id(data: bytes) -> dict[str, int]:
    if len(data) != 7:
        raise ValueError("invalid_length")
    return {
        "vendor_id_source": data[0],
        "vendor_id": int.from_bytes(data[1:3], "little"),
        "product_id": int.from_bytes(data[3:5], "little"),
        "product_version": int.from_bytes(data[5:7], "little"),
    }


@dataclass(frozen=True)
class DeviceInformationCharacteristic:
    """A known read: owning service, characteristic and platform-neutral decoder."""

    uuid: str
    service_uuid: str = DEVICE_INFORMATION_SERVICE_UUID
    decode: Callable[[bytes], str | dict[str, int]] = _text

    def belongs_to(self, service_uuid: str, characteristic_uuid: str) -> bool:
        return (service_uuid.lower() in (self.service_uuid, self.service_uuid[4:8])
                and characteristic_uuid.lower() in (self.uuid, self.uuid[4:8]))


DEVICE_INFORMATION_CHARACTERISTICS = {
    "device_name": DeviceInformationCharacteristic("00002a00-0000-1000-8000-00805f9b34fb", _GAP_SERVICE_UUID),
    "manufacturer": DeviceInformationCharacteristic("00002a29-0000-1000-8000-00805f9b34fb"),
    "model": DeviceInformationCharacteristic("00002a24-0000-1000-8000-00805f9b34fb"),
    "firmware": DeviceInformationCharacteristic("00002a26-0000-1000-8000-00805f9b34fb"),
    "hardware": DeviceInformationCharacteristic("00002a27-0000-1000-8000-00805f9b34fb"),
    "software": DeviceInformationCharacteristic("00002a28-0000-1000-8000-00805f9b34fb"),
    "serial": DeviceInformationCharacteristic("00002a25-0000-1000-8000-00805f9b34fb"),
    "pnp_id": DeviceInformationCharacteristic("00002a50-0000-1000-8000-00805f9b34fb", decode=_pnp_id),
}
DEVICE_INFORMATION_TIMEOUT = 2.0
_PROBE_TIMEOUT = 15.0


@dataclass(frozen=True)
class BleDeviceInformation:
    """Endpoint-tagged snapshot; missing fields are normal, read failures explicit.

    Values can identify the Bluetooth module rather than the printer. They must
    not select a printer protocol. service_present describes DIS (180A), not
    GAP; a GAP device_name may be available even when DIS is absent.
    values contains UTF-8 strings and a structured pnp_id dictionary.
    """

    address: str
    read_at: str
    service_present: bool
    values: dict[str, str | dict[str, int]]
    errors: dict[str, str]

    @classmethod
    def from_gatt(
        cls, address: str, *, service_present: bool,
        values: dict[str, bytes], errors: dict[str, str],
    ) -> BleDeviceInformation:
        """Decode the same raw characteristic results on every platform."""
        decoded = {}
        failures = {}
        for field, definition in DEVICE_INFORMATION_CHARACTERISTICS.items():
            uuid = definition.uuid
            if uuid in errors:
                failures[field] = errors[uuid]
            elif uuid in values:
                try:
                    decoded[field] = definition.decode(values[uuid])
                except ValueError as exc:
                    failures[field] = str(exc)
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
        values = {}
        errors = {}
        for name in DEVICE_INFORMATION_CHARACTERISTICS:
            value = self.values.get(name, "")
            if name == "pnp_id" and isinstance(value, dict):
                value = {key: number for key, number in value.items()
                         if key in ("vendor_id_source", "vendor_id", "product_id", "product_version")
                         and type(number) is int and 0 <= number <= 65535}
            else:
                value = reporting.device_metadata_text(value)
            if value is not None:
                values[name] = value
            if name in self.errors:
                error = self.errors[name]
                errors[name] = error if error in ("timeout", "not_readable", "invalid_utf8", "invalid_length") else "read_failed"
        reporter.debug(short="BLE device information", detail=json.dumps({
            "service_present": self.service_present, "values": values,
            "available_fields": sorted(name for name in DEVICE_INFORMATION_CHARACTERISTICS if name in self.values),
            "errors": errors,
        }))
        reporter.trace(short="BLE device information raw", detail=json.dumps(self.as_dict()))


async def read_device_information(
    client, address: str, *, timeout: float = DEVICE_INFORMATION_TIMEOUT,
) -> BleDeviceInformation:
    """Read known GAP/DIS fields on an already connected Bleak-compatible client.

    No writes, pairing, notification subscription or printer commands. One total
    deadline bounds optional reads. Cancellation remains cancellation, not a
    metadata failure. After a timed-out read do not queue more GATT requests.
    """
    services = tuple(getattr(client, "services", None) or ())
    service_present = any(str(item.uuid).lower() in (DEVICE_INFORMATION_SERVICE_UUID, "180a") for item in services)
    values: dict[str, bytes] = {}
    errors: dict[str, str] = {}
    deadline = monotonic() + timeout
    for definition in DEVICE_INFORMATION_CHARACTERISTICS.values():
        uuid = definition.uuid
        characteristic = next((char for service in services for char in service.characteristics
                               if definition.belongs_to(str(service.uuid), str(char.uuid))), None)
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
        address, service_present=service_present, values=values, errors=errors,
    )


async def probe_ble_device_information(address: str) -> BleDeviceInformation:
    """Read without a profile; bound setup to 15 seconds and cleanup separately."""
    from bleak import BleakClient

    client = BleakClient(address, timeout=_PROBE_TIMEOUT)
    lifecycle = _BleakLifecycle()

    async def read():
        await client.connect()
        return await read_device_information(client, address)

    error = None
    try:
        return await lifecycle.run(read(), timeout=_PROBE_TIMEOUT, label="device information probe")
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            await lifecycle.disconnect(client)
        except Exception as cleanup_error:
            if error is not None:
                raise error from cleanup_error
            raise
