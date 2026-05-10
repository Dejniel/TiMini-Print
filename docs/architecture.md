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
It describes printer capabilities and tuning.

A `PrinterProfile` is not enough to print by itself.
It does not say:
- which protocol family is active right now
- which protocol variant is active right now
- which image pipeline is active right now
- which transport target is active right now

### `PrinterDevice`
The central runtime object.
It combines:
- display name
- profile
- protocol family
- optional protocol variant
- image pipeline
- runtime metadata
- optional transport target

If code needs to talk about “this actual printer instance as we currently intend to use it”, it should normally use `PrinterDevice`.

### `PrinterProtocol`
A protocol builder bound to one `PrinterDevice`.
It turns raster input into a `ProtocolJob`.

Important: `PrinterProtocol` is not a transport object.
It builds jobs; it does not connect or send.

### `ProtocolJob`
A unit of work that transport can send.
It contains:
- `payload`
- optional `steps`
- `runtime_controller`

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

### `BluetoothDiscovery`
This is transport-facing discovery.
It does scan hardware.
It returns reachable Bluetooth printers as `PrinterDevice` objects and can also select one by name or address.

This split keeps device knowledge out of transport while still allowing discovery to produce fully resolved runtime objects.

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
- detection rules
- `PrinterCatalog`
- device config serialization

### `timiniprint.raster`
Owns shared raster data types.

It exists so that rendering and protocol can share raster types without importing each other.

### `timiniprint.rendering`
Owns file and page processing.

It contains:
- file loading
- page transforms
- rasterization

### `timiniprint.protocol`
Owns stateless protocol building.

It contains:
- packet builders
- family-specific stateless logic
- `PrinterProtocol`
- `ProtocolJob`
- internal low-level builders in `_builders`

### `timiniprint.printing`
Owns the higher-level file pipeline and stateful runtime logic.

It contains:
- `PrintJobBuilder`
- `PrintSettings`
- runtime controllers in `printing.runtime`

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
- devices should describe printers, not perform I/O

## Stateful runtime behavior

There are two kinds of logic in the codebase:

1. stateless protocol building
2. stateful session behavior

Examples:
- packet formats belong in `timiniprint.protocol.families.*`
- named print-job operation plans belong in `ProtocolJob.steps`
- temperature/status-driven session behavior belongs in `timiniprint.printing.runtime.*`

This split matters because some printer families need session state during transport, but packet construction still needs to stay reusable outside the built-in app flow.

## Bluetooth-specific note

Bluetooth discovery and Bluetooth connection are separate concerns.

- `BluetoothDiscovery` finds and resolves printers into `PrinterDevice`
- `BleakBluetoothConnector` connects and sends jobs for those devices

That keeps discovery logic out of protocol code and keeps transport replaceable.

## Where to put new code

Use this rule of thumb:

- put it in `devices` if it changes how a printer is described or detected
- put it in `rendering` if it changes how files become raster data
- put it in `protocol` if it changes stateless packet building
- put it in `printing.runtime` if it changes stateful session behavior
- put it in `transport` if it changes actual connection or write mechanics
