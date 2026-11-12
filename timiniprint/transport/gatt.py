"""Platform-neutral GATT discovery metadata for connection diagnostics."""
from __future__ import annotations

from uuid import UUID


def gatt_uuid(value: str) -> str:
    """Format an actual GATT UUID, never an arbitrary identifier or address."""
    try:
        if len(value) in (4, 8):
            value = f"{value.zfill(8)}-0000-1000-8000-00805f9b34fb"
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return "<unavailable>"


def gatt_inventory(services) -> list[dict]:
    """Service/characteristic UUIDs and known properties, without values."""
    return [{"service": gatt_uuid(service.uuid), "characteristics": [
        {"uuid": gatt_uuid(char.uuid), "properties": [prop for prop in char.properties
         if prop in ("read", "write", "write-without-response", "notify", "indicate")]}
        for char in service.characteristics]} for service in services or ()]


def is_gatt_metadata_service(service_uuid: str) -> bool:
    """GAP, GATT, Device Information and Battery are not a printer byte stream.

    Check the owning service, not just characteristic UUIDs which may also
    appear in vendor services. Other services remain available to the runtime.
    """
    return gatt_uuid(service_uuid) in {
        "00001800-0000-1000-8000-00805f9b34fb",
        "00001801-0000-1000-8000-00805f9b34fb",
        "0000180a-0000-1000-8000-00805f9b34fb",
        "0000180f-0000-1000-8000-00805f9b34fb",
    }
