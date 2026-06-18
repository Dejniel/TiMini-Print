# Architecture

Read [protocol.md](protocol.md) first.
This document is the second step: it explains why the public API looks the way it does and where code belongs internally.

## The main runtime model

The codebase is built around three different concerns that stay separate on purpose:

1. describe a concrete printer
2. build a printable job for that printer
3. send that job over some transport

That is why the public model is built from:
- `PrinterDevice`
- `PrinterProtocol`
- connectors

## Main objects

### `PrinterProfile`
Static catalog data.
It describes printer capabilities and print defaults.

`print_size` is the source catalog value.
`profile.width` is the raster width the rendering layer should produce.
For some TinyPrint A4-style models these differ because the original app renders
to `paper_size` and the protocol recipe adds left-side padding before packing
the wire payload.
TinyPrint size-8 paper handling is modeled as `paper_mode`: `plain` keeps the
roll-paper feed recipe, while `a4_sheet` applies the original A4-sheet feed
recipe for that protocol variant.

A `PrinterProfile` is not enough to print by itself.
It does not say:
- which protocol family is active right now
- which protocol variant is active right now
- which image pipeline is active right now
- which runtime control algorithm, preset, and capabilities are active right now
- which transport target is active right now

### `SupportedPrinterModel`
Catalog model data.
It describes a source-backed printer model or clone:
- named Bluetooth detections, where each public model name is attached to its
  own exact names, prefixes, and optional MAC suffix constraints
- original Android app package names
- the shared `PrinterProfile` key to use
- optional protocol/runtime overrides for this model

Several supported models may intentionally point to the same `PrinterProfile`
when they use the same protocol recipe. If two source apps use the same advertised
name with different values, model both variants explicitly and keep automatic
detection conservative.

Editable printer configs should normally keep `model_key` as the fallback.
That preserves model-level protocol overrides, image pipeline overrides,
runtime presets, and source-app metadata when users delete individual override
fields. Raw profile-based configs are low-level diagnostics only.

### `UnsupportedPrinterModel`
Catalog model data for known-but-not-implemented printers.
It has named detections and optional source app packages, but no
`PrinterProfile`.

Use it to recognize reports and future-support candidates without pretending the
printer is printable. `catalog.detect_model(...)` may include these records in
its match tuple; `catalog.detect_device(...)` must not return them.
`model_group`, when present, is only a public inventory grouping hint for
clone-like TODO names. It is not a profile key and must not route unsupported
hardware to an implemented protocol.

### `RuntimeSettings`
Runtime catalog data.
It describes stateful print-session behavior that is not part of the static printer profile:
- `control_algorithm`: which runtime algorithm to use
- `preset`: dynamic density inputs for that algorithm, when the protocol needs them
- `capabilities`: status-notification features used by the runtime controller

This exists so dynamic V5G/MX density behavior does not have to borrow a second
`PrinterProfile` just to get density inputs.

### `PrinterDevice`
The central runtime object.
It combines:
- display name
- profile
- protocol family
- optional protocol variant
- image pipeline
- runtime settings
- optional transport target

If code needs to talk about “this actual printer instance as we currently intend to use it”, it should normally use `PrinterDevice`.

### `PrinterProtocol`
A protocol builder bound to one `PrinterDevice`.
It turns raster input into a `ProtocolJob`.

Important: `PrinterProtocol` is not a transport object.
It builds jobs; it does not connect, send, or create runtime controllers.
Stateful runtime controllers are attached by the `printing` layer.

### `ProtocolJob`
A unit of work that transport can send.
It contains:
- `payload`
- optional `steps`
- optional `runtime_controller` supplied by the printing/runtime layer

`payload` is the stream-only representation.
`steps` is the named protocol operation plan for families that need request/response control flow during a print job.
The transport still sees only generic sends and queries; it does not learn family-specific command meaning.

### Connectors
Connectors handle real I/O.
Repo implementations include:
- `BleakBluetoothConnector`
- `SerialConnector`

A connector connects using `PrinterDevice` and sends `ProtocolJob`.

## Detecting versus discovering

The codebase has two different concepts and they are intentionally separate.

### `PrinterCatalog.detect_device(...)`
This is catalog-level detection.
It does not scan hardware.
It takes an already known device name and optional address and maps them to a `PrinterDevice`.
More specific unsupported metadata can prevent a broad supported prefix from
stealing an unrelated model. When supported and unsupported matches have the same
specificity, supported wins. If multiple supported models tie, `detect_device(...)`
returns `None` so the caller can ask the user to choose the source app or model
explicitly.

### `BluetoothDiscovery`
This is transport-facing discovery.
It does scan hardware.
It returns reachable Bluetooth printers as `PrinterDevice` objects and can also select one by name or address.
Automatic discovery keeps only unambiguous printable devices. UI and CLI scan
views should call `devices_for_display(...)` to include manual candidates for
source-app/model conflicts.
It delegates raw endpoint resolution to `BluetoothEndpointResolver` in `timiniprint.devices`.

This split keeps device knowledge out of transport while still allowing discovery to produce fully resolved runtime objects.
BLE MTU requests are profile/device hints: `devices` decides whether a model
should request a custom MTU, while `transport` decides whether the current backend can apply that request.
Missing `ble_mtu_request` means the default `512` request; explicit `23`
means standard BLE MTU and keeps the conservative default write payload.
Transport code owns scanning; devices code owns turning raw endpoints into logical printer devices.

## Why protocol and transport are separate

This split is the important architectural decision.

It allows these combinations:
- repo discovery + repo transport
- repo discovery + custom transport
- explicit `PrinterDevice` + repo transport
- explicit `PrinterDevice` + custom transport
- `PrinterProtocol` only, with no repo transport at all

That is why the code does not use a model like `Protocol(connector).send(...)`.
Doing that would collapse packet building and transport into one object and make reuse harder.

Instead, the shared object is `PrinterDevice`.
That keeps protocol and transport aligned without making either one own the other.

## Package roles

### `timiniprint.devices`
Owns printer description and detection.

It contains:
- `PrinterDevice`
- `PrinterProfile`
- `SupportedPrinterModel`
- `UnsupportedPrinterModel`
- Bluetooth endpoint and target models
- `BluetoothEndpointResolver` for raw Bluetooth endpoint merging and catalog matching
- model detection
- `PrinterCatalog`
- config serialization

### `timiniprint.raster`
Owns shared raster data types.

It exists so that rendering and protocol can share raster types without importing each other.

### `timiniprint.rendering`
Owns file and page processing.

It contains:
- low-level file converters
- page sources for one-page-at-a-time conversion
- page transforms
- rasterization primitives

### `timiniprint.protocol`
Owns stateless protocol building.

It contains:
- packet builders
- family-specific stateless logic
- `PrinterProtocol`
- `ProtocolJob`
- protocol-facing runtime capability data that can affect payload selection
- internal low-level builders in `_builders`

### `timiniprint.printing`
Owns the higher-level file pipeline and stateful runtime logic.

It contains:
- `PrintJobBuilder`
- `DocumentRenderer`, the printing-layer bridge from documents to raster pages
- `PrintSettings`
- streaming page-job assembly for memory-sensitive callers
- runtime controllers in `printing.runtime`

`DocumentRenderer` uses `timiniprint.rendering` converters, but it lives in
`printing` because it also needs printer settings, resolved protocol image
pipeline choices, and runtime capabilities. `PrintJobBuilder` does not own file
conversion directly; it asks `DocumentRenderer` for rendered pages, applies any
print-job-only debug markers, and builds `ProtocolJob` pages.

### `timiniprint.transport`
Owns actual I/O.

It contains:
- connector interfaces
- connection implementations
- Bluetooth and serial transport code

## Dependency direction

The intended flow is:
- `rendering -> raster`
- `devices -> protocol.family|protocol.types`
- `protocol -> raster`
- `printing -> devices`
- `printing -> rendering`
- `printing -> protocol`
- `transport -> devices`
- `transport -> protocol`

The important practical rule is:
- rendering should not depend on protocol builders
- protocol should not depend on transport
- protocol should not depend on printing runtime controllers
- devices should describe printers, not perform I/O

## Stateful runtime behavior

There are two kinds of logic in the codebase:

1. stateless protocol building
2. stateful session behavior

Examples:
- packet formats belong in `timiniprint.protocol.families.*`
- named print-job operation plans belong in `ProtocolJob.steps`
- session-derived protocol inputs, such as print capabilities discovered at runtime, can be passed into protocol builders as data
- temperature/status-driven session behavior belongs in `timiniprint.printing.runtime.*`

This split matters because some printer families need session state during transport, but packet construction still needs to stay reusable outside the built-in app flow.

## Bluetooth-specific note

Bluetooth discovery and Bluetooth connection are separate concerns.

- `BluetoothDiscovery` scans hardware and asks `BluetoothEndpointResolver` to resolve printers into `PrinterDevice`
- `BleakBluetoothConnector` connects and sends jobs for those devices

That keeps discovery logic out of protocol code and keeps transport replaceable.
It also lets another platform-specific scanner, such as a mobile native bridge,
reuse the same endpoint-resolution behavior without using the desktop Bluetooth backend.

## Where to put new code

Use this rule of thumb:

- put it in `devices` if it changes how a printer is described or detected
- put it in `devices` if it changes how raw Bluetooth endpoints are merged into logical printer devices
- put it in `rendering` if it changes how files become raster data
- put it in `protocol` if it changes stateless packet building
- put it in `printing.runtime` if it changes stateful session behavior
- put it in `transport` if it changes actual connection or write mechanics
