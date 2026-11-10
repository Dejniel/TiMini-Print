"""Shared BLE UART channel hints, not printer/model identification.

These UUIDs rank discovered channels. They never establish a wire protocol and
must not fabricate a service or characteristic absent from the discovery result.
Family-specific channel preferences remain in BleTransportProfile.
"""
from __future__ import annotations

BLE_UART_SERVICES = frozenset((
    "0000ae30-0000-1000-8000-00805f9b34fb",
    "0000ae3a-0000-1000-8000-00805f9b34fb",
    "0000ae00-0000-1000-8000-00805f9b34fb",
    "0000ae80-0000-1000-8000-00805f9b34fb",
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "0000ff00-c243-40b9-ac41-a10ba3ad5d1a",
    "0000ab00-0000-1000-8000-00805f9b34fb",
    "49535343-fe7d-4ae5-8fa9-9fafd205e455",
))
BLE_UART_WRITERS = frozenset((
    "0000ae01-0000-1000-8000-00805f9b34fb",
    "0000ae3b-0000-1000-8000-00805f9b34fb",
    "0000ae81-0000-1000-8000-00805f9b34fb",
    "0000ff02-0000-1000-8000-00805f9b34fb",
    "0000ff02-c243-40b9-ac41-a10ba3ad5d1a",
    "0000ab01-0000-1000-8000-00805f9b34fb",
    "49535343-8841-43f4-a8d4-ecbe34729bb3",
))
BLE_UART_READERS = frozenset((
    "0000ae02-0000-1000-8000-00805f9b34fb",
    "0000ff01-0000-1000-8000-00805f9b34fb",
    "0000ff01-c243-40b9-ac41-a10ba3ad5d1a",
    "0000ff03-0000-1000-8000-00805f9b34fb",
    "0000ff03-c243-40b9-ac41-a10ba3ad5d1a",
    "0000ae04-0000-1000-8000-00805f9b34fb",
    "0000ae3c-0000-1000-8000-00805f9b34fb",
    "0000ab03-0000-1000-8000-00805f9b34fb",
    "49535343-1e4d-4bd9-ba61-23c647249616",
))


def ble_uuid(value: str) -> str:
    """Canonical comparison form, also preserving opaque test/vendor names."""
    value = value.strip().lower()
    if len(value) in (4, 8):
        try:
            int(value, 16)
        except ValueError:
            pass
        else:
            value = f"{value.zfill(8)}-0000-1000-8000-00805f9b34fb"
    return value
