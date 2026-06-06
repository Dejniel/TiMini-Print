# Protocol Guide

Start here if you want to use TiMini-Print from your own code.

This guide is intentionally practical.
It answers one question first:

- how do I print something with the current public API?

Read it in this order:

1. get or create a `PrinterDevice`
2. build a `ProtocolJob`
3. send that job through a connector

If you want package boundaries and internal placement after that, read [architecture.md](architecture.md).

## The core objects

### `PrinterDevice`
`PrinterDevice` is the central runtime object.
It carries the resolved:
- profile
- protocol family
- optional protocol variant
- image pipeline
- optional runtime settings
- optional transport target

A `PrinterDevice` is the one object that both protocol code and transport code agree on.

Runtime settings are separate from the static profile:
- `variant` selects the stateful runtime algorithm, when a family needs one
- `defaults` provides runtime density defaults such as V5G/MX dynamic density inputs
- `capabilities` describes runtime notification features used by the controller

Editable JSON configs keep this separation too: `profile_overrides` edits the
static profile, while `runtime_overrides` edits runtime variant, defaults key,
density, and capabilities.

### `PrintJobBuilder`
`PrintJobBuilder(device, settings=...)` is the normal high-level entry point.
Use it when you start from a file such as `.png`, `.jpg`, `.pdf`, or `.txt`.

It handles:
- file loading
- page transforms
- rasterization
- protocol job building

### `PrinterProtocol`
`PrinterProtocol(device)` is the lower-level protocol entry point.
Use it only when you already have raster data and want to build a printable job directly.
It does not create runtime controllers; callers that need stateful runtime behavior
must pass runtime capabilities/controller data from the printing layer.

### `ProtocolJob`
A `ProtocolJob` is what transport sends.
It contains:
- `payload: bytes`
- optional `steps`, a named send/query plan for protocols that need replies during a print job
- optional `runtime_controller`, supplied by the printing/runtime layer for families that need session behavior during transport

### Connectors
Connectors handle actual I/O.
The repo provides:
- `BleakBluetoothConnector`
- `SerialConnector`

A connector accepts a `PrinterDevice`, opens a connection, and sends a `ProtocolJob`.

## First example: print a file over Bluetooth

This is the best first contact with the API.

```python
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.settings import PrintSettings
from timiniprint.transport.bluetooth import BluetoothDiscovery, BleakBluetoothConnector

catalog = PrinterCatalog.load()
discovery = BluetoothDiscovery(catalog)

# Scan nearby supported printers and pick the first one.
devices = await discovery.scan_devices()
if not devices:
    raise RuntimeError("No supported printers found")
device = devices[0]

# Open a connection, prepare any live runtime capabilities, then build and send.
connection = await BleakBluetoothConnector().connect(device)
try:
    runtime_context = await prepare_connection_runtime(device, connection)
    job = PrintJobBuilder(
        device,
        settings=PrintSettings(
            blackening=3,
            rotate_90_clockwise=False,
        ),
        runtime_context=runtime_context,
    ).build_from_file("example.png")
    await connection.send(job)
finally:
    await connection.disconnect()
```

What this does:
- `BluetoothDiscovery` finds reachable printers and returns `PrinterDevice` objects
- `PrintJobBuilder` turns the file into a `ProtocolJob`
- `prepare_connection_runtime` and `PrintJobBuilder` attach runtime controller state when the selected family needs it
- `BleakBluetoothConnector` uses `device.transport_target` and `device.profile.stream`
- the caller does not manually pass `chunk_size`, `delay_ms`, or `runtime_controller`

Use this path when:
- you want the repo file pipeline
- you are printing normal files, not prebuilt raster data
- you are fine using the repo Bluetooth transport

If you build a job directly with `PrinterProtocol`, you are using the stateless
protocol layer only. That path can still accept runtime capabilities, but it will
not create a runtime controller for you.

## Choosing a specific Bluetooth printer

The first example used `scan_devices()` and picked `devices[0]` to avoid introducing a fake or magic printer name.

If you want to select one specific discovered printer, use:

```python
device = await discovery.resolve_device("AA:BB:CC:DD:EE:01")
```

or:

```python
device = await discovery.resolve_device("X6H-ABCD")
```

`resolve_device(...)` in `BluetoothDiscovery` means:
- scan for real Bluetooth devices
- pick one discovered device by name or address

It is different from `catalog.detect_device(...)`, which does not scan Bluetooth at all.

## Known profile, no discovery

Use this when Bluetooth discovery is not involved and you already know what printer profile should be used.

```python
from timiniprint.devices import PrinterCatalog, SerialTarget
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.transport.serial import SerialConnector

catalog = PrinterCatalog.load()

# Create a PrinterDevice directly from a known profile.
device = catalog.device_from_profile(
    "a200",
    transport_target=SerialTarget("/dev/rfcomm0"),
)

connection = await SerialConnector().connect(device)
try:
    runtime_context = await prepare_connection_runtime(device, connection)
    job = PrintJobBuilder(device, runtime_context=runtime_context).build_from_file("example.pdf")
    await connection.send(job)
finally:
    await connection.disconnect()
```

Use this path when:
- you already know the profile key
- discovery is not part of your flow
- you want an explicit `PrinterDevice` up front

## Use the protocol with your own connector

This is the main extension point if you do not want to use the repo Bluetooth implementation.

A custom connector is expected to do three things:
- connect using a `PrinterDevice`
- send a `ProtocolJob`
- disconnect cleanly

If you want full support for query-driven families, your connection should also satisfy the optional
`RuntimeProbeConnection` contract from `timiniprint.transport.base` and expose:
- `attach_runtime_controller(runtime_controller, *, timeout=...)`
- `send_control_packet(packet, *, timeout=...)`
- `query_control_packet(packet, *, timeout=...)`

If your transport cannot support these operations, you can omit them. Runtime-sensitive
families will then fall back to degraded behavior when possible.

A minimal sketch looks like this:

```python
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.runtime.prepare import prepare_connection_runtime


class MyConnection:
    def __init__(self, raw_link, device):
        self._raw_link = raw_link
        self._device = device

    async def attach_runtime_controller(self, runtime_controller, *, timeout=1.0):
        # Optional: bridge runtime session setup into your transport if needed.
        _ = runtime_controller, timeout

    async def send_control_packet(self, packet, *, timeout=1.0):
        # Optional: return False if your transport cannot send standalone control packets.
        _ = packet, timeout
        return False

    async def query_control_packet(self, packet, *, timeout=1.0):
        # Optional: return None if your transport cannot do request/response queries.
        _ = packet, timeout
        return None

    async def send(self, job):
        # Use the stream settings resolved for this device.
        chunk_size = self._device.profile.stream.chunk_size
        delay_ms = self._device.profile.stream.delay_ms

        # Your transport implementation is responsible for writing job.payload.
        # If the target family needs session logic, job.runtime_controller must be
        # honored during the write loop.
        await send_payload_over_my_link(
            self._raw_link,
            payload=job.payload,
            chunk_size=chunk_size,
            delay_ms=delay_ms,
            runtime_controller=job.runtime_controller,
        )

    async def disconnect(self):
        await self._raw_link.close()


class MyConnector:
    async def connect(self, device):
        # Open your own transport here. You can use device.transport_target,
        # or ignore it and use your own connection settings.
        raw_link = await open_my_link(device.transport_target)
        return MyConnection(raw_link, device)


catalog = PrinterCatalog.load()
device = catalog.device_from_profile("x6h")

connection = await MyConnector().connect(device)
try:
    runtime_context = await prepare_connection_runtime(device, connection)
    job = PrintJobBuilder(device, runtime_context=runtime_context).build_from_file("example.png")
    await connection.send(job)
finally:
    await connection.disconnect()
```

This is the main reason protocol and transport stay separate in the architecture.
The repo should make it easy to reuse protocol logic without forcing one Bluetooth stack.
If you want probe warnings to go somewhere visible, pass your own reporter into
`prepare_connection_runtime(..., reporter=...)`.

## Detecting by name is not the same as Bluetooth discovery

There are two different operations in the codebase and they should not be confused.

### `catalog.detect_device(...)`
This does not scan hardware.
It applies detection rules to a known device name and optional address.

Use it when you already have values such as:
- a BLE name from somewhere else
- a saved MAC address
- a stored printer config flow that still wants catalog-backed detection

Example:

```python
from timiniprint.devices import PrinterCatalog

catalog = PrinterCatalog.load()
device = catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:59")
if device is None:
    raise RuntimeError("Printer profile not detected")
```

### `BluetoothDiscovery`
This does scan hardware.
It returns real reachable Bluetooth printers as `PrinterDevice` objects.

Use it when your program needs to find printers nearby.

## Save and reload an editable printer config

Use this when you want to inspect or tweak the resolved runtime values instead of relying on auto-detection every time.

```python
from pathlib import Path
import json

from timiniprint.devices import PrinterCatalog

catalog = PrinterCatalog.load()
device = catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:59")
if device is None:
    raise RuntimeError("Printer profile not detected")

# Export an editable printer config. profile_key remains the catalog fallback;
# profile_overrides can be edited or partially deleted.
printer_config = catalog.serialize_printer_config(device)
Path("printer.json").write_text(
    json.dumps(printer_config, indent=2) + "\n",
    encoding="utf-8",
)

# Load it back later as an explicit PrinterDevice.
loaded = json.loads(Path("printer.json").read_text(encoding="utf-8"))
manual_device = catalog.device_from_printer_config(loaded)
```

This printer config captures:
- base profile key
- full editable profile overrides
- protocol family
- image pipeline
- runtime overrides: variant, defaults key, density, and capabilities
- optional transport target

This is the right tool if you want to experiment with runtime values without adding more one-off CLI flags.

## Advanced: build jobs from raster data

Only use this path when you already have raster data and do not want the repo file/rendering pipeline.

Two raster types matter here:

- `RasterBuffer`
  - one raster in one pixel format
  - `pixels` is a flat row-major sequence
  - `width` defines where rows break
- `RasterSet`
  - one or more rasters for the same page, keyed by `PixelFormat`
  - all rasters in the set must have matching dimensions

Supported public pixel formats are:
- `PixelFormat.BW1`
  - binary raster values `0` or `1`
- `PixelFormat.GRAY4`
  - grayscale values `0..15`
- `PixelFormat.GRAY8`
  - grayscale values `0..255`

Most callers do not need to build these manually, because `PrintJobBuilder` and the rendering pipeline already do it.

If you do want the low-level path, it looks like this:

```python
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet

catalog = PrinterCatalog.load()
device = catalog.detect_device("X6H-ABCD")
if device is None:
    raise RuntimeError("Printer profile not detected")

raster = RasterBuffer(
    pixels=[1] * 64,
    width=8,
    pixel_format=PixelFormat.BW1,
)
raster_set = RasterSet.from_single(raster)

protocol = PrinterProtocol(device)
job = protocol.build_job(
    raster_set,
    is_text=False,
    blackening=3,
    feed_padding=12,
)
```

Use this path when:
- you already have raster data
- you want protocol building without file loading
- you want to plug the built job into your own transport layer

The old function-style raw builders are internal implementation details in `timiniprint.protocol._builders`.
They still exist for internal composition and tests, but they are not the public integration surface anymore.

## Mental model

If you are unsure which API level to use, use this rule:

- `PrintJobBuilder` if you start from a file
- `PrinterProtocol` if you start from raster data
- a connector if you need actual I/O
- `PrinterDevice` as the shared object passed between them

The important thing to avoid is treating `PrinterProtocol` as a connection object.
It is not `Protocol(connector).send(...)`.
Its job is to build a `ProtocolJob` for a specific `PrinterDevice`.

## Where to add new functionality

### Add it to `timiniprint.rendering` when it is about pages or images
Examples:
- a new file loader
- a new page transform
- image preprocessing before rasterization

### Add it to `timiniprint.protocol` when it is about stateless packet building
Examples:
- packet builders
- compression/encoding changes
- family-specific job construction

### Add it to `timiniprint.printing.runtime` when it depends on session state
Examples:
- notify handling
- status polling
- temperature-driven behavior
- stateful BLE write logic

Rule of thumb:
- if it depends only on input data, it belongs in `protocol`
- if it depends on session state, notify packets, timing, or previous writes, it belongs in `printing.runtime`

### Add it to `timiniprint.devices` when it is about describing or detecting printers
Examples:
- profile data models
- profile loading
- detection rules
- `PrinterDevice` creation
- printer config serialization

### Add it to `timiniprint.transport` when it is about actual I/O
Examples:
- a new connector
- a new connection implementation
- transport-specific connection or write behavior
