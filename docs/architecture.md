# Architecture

Read [protocol.md](protocol.md) first if you want to use TiMini-Print from your own code. Read [catalog.md](catalog.md) for profile/model JSON data. This document is about package boundaries and where code belongs.

## Runtime Flow

The app-level flow is:

1. `devices` resolves a `PrinterDevice`
2. `transport` opens a connector-specific connection
3. `printing.connected.connect_printer(...)` prepares runtime state
4. `ConnectedPrinter` prints files/text or sends prepared jobs
5. `protocol` builds packet payloads and optional protocol steps
6. `transport` writes bytes and exposes generic query/wait primitives

The important object at runtime is `PrinterDevice`. It is the shared description used by protocol, printing, and transport without making those packages own each other.

## Package Boundaries

### `timiniprint.devices`
Owns printer description and catalog resolution.

It contains `PrinterDevice`, model/profile data classes, `PrinterCatalog`, config serialization, Bluetooth endpoint models, BLE transport profiles, and `BluetoothEndpointResolver`. It may decide which logical printer a raw endpoint represents and select the ready-to-use BLE profile for it. It must not perform I/O.

### `timiniprint.printing`
Owns the file-to-job flow and stateful print-session behavior.

It contains `ConnectedPrinter`, `connect_printer`, `PrintJobBuilder`, `DocumentRenderer`, `PrintSettings`, send helpers, and runtime controllers. This package is allowed to coordinate devices, protocol, rendering, and transport because it is the app-level print orchestration layer.

### `timiniprint.protocol`
Owns stateless wire-format construction.

It contains `PrinterProtocol`, `ProtocolJob`, protocol families, packet builders, image encoding choices, paper-mode recipe values, and internal low-level builders. It must not connect to hardware, scan Bluetooth, or know transport adapters.

### `timiniprint.rendering`
Owns files, pages, converters, transforms, and rasterization.

It should not know printer protocols. `printing.DocumentRenderer` bridges rendering output into printer-specific job building because that step needs printer settings and selected paper/image pipeline.

### `timiniprint.raster`
Owns shared raster types.

It exists so rendering and protocol can share `RasterBuffer`, `RasterSet`, and pixel formats without importing each other.

### `timiniprint.transport`
Owns actual I/O.

It contains connector interfaces, connection implementations, Bluetooth adapters, and serial transport code. Transport may expose generic send/query/wait primitives. Bluetooth adapters receive a selected BLE profile and apply its GATT endpoints, chunk limits, and pacing; they do not select behavior from a protocol family. Transport must not contain printer-family opcode logic or firmware-state decisions.

## Main Objects

### `PrinterDevice`
A resolved printer instance as the program intends to use it. It combines display name, profile, protocol family, protocol variant, image pipeline, runtime settings, paper presets, optional transport target, and the BLE transport profile derived by the devices layer.

### `ConnectedPrinter`
The high-level object for an active printer session. It owns an active connection and prepared runtime context, then exposes `print_file(...)`, `print_text(...)`, `send_job(...)`, `feed()`, `retract()`, and `disconnect()`.

CLI and GUI should use `ConnectedPrinter` instead of manually combining `PrintJobBuilder`, runtime preparation, and `send_prepared_job`.

### `PrintJobBuilder`
A lower-level file-to-job builder. It turns files into `ProtocolJob` objects using `DocumentRenderer` and `PrinterProtocol`. It does not own connection lifetime or runtime preparation.

Use it directly for preview/debug/streaming-page workflows where a caller wants jobs without immediately printing them.

Renderer pages are not always physical pages. Text is divided into bounded raster chunks but marked as one continuous page flow; PDF and image plans remain paged. `PrintJobBuilder` carries that distinction into protocol requests so family recipes can omit intermediate paper positioning without making protocol code depend on file types.

### `PrinterProtocol`
A protocol builder bound to one `PrinterDevice`. It builds `ProtocolJob` from raster input and may produce named protocol steps for families that need interleaved send/query/wait operations.

Internal family builders return one `ProtocolPlan` shape for both stream-only and step-based protocols. The public `ProtocolJob` wraps that stateless plan with job-level execution policy.

It is not a connection object. Do not add `Protocol(connector).send(...)` style APIs.

### `ProtocolJob`
A stateless protocol execution plan. It contains payload bytes, optional payload segments, optional named steps, and whether printing should wait for protocol completion. It does not contain a live runtime controller. Stream-only jobs can be sent directly by a connection; jobs with steps must go through `ConnectedPrinter.send_job(...)` or `send_prepared_job(...)`.

Transport sees generic send/query/wait operations. It does not learn family-specific command meaning.

### Connectors And Connections
A connector connects using a resolved `PrinterDevice` and returns a connection. A connection can send a stream-only `ProtocolJob` and disconnect. Some connections also support the generic operations needed by the printing layer to execute step-based jobs, such as control-packet send/query, bulk send, and notification waits.

Most app-level code should pass a connector into `connect_printer(...)` and use the returned `ConnectedPrinter`.

## Protocol And Transport Separation

Protocol and transport stay separate so these combinations remain possible:

- repo discovery + repo transport + `ConnectedPrinter`
- repo discovery + custom transport + `ConnectedPrinter`
- explicit `PrinterDevice` + repo transport + `ConnectedPrinter`
- explicit `PrinterDevice` + custom transport + `ConnectedPrinter`
- `PrinterProtocol` only, with no repo transport at all

Packet construction belongs in `protocol`. Connection mechanics belong in `transport`. Stateful protocol synchronization belongs in `printing.runtime`, because it sits between the packet plan and the live connection.

## Stateful Runtime Behavior

There are two kinds of protocol-related behavior:

- stateless packet building
- stateful session behavior

Stateless packet formats belong in `timiniprint.protocol.families.*`. Runtime behavior belongs in `timiniprint.printing.runtime.*` when it depends on current session state, notifications, timing, previous writes, firmware replies, or completion waits.

`prepare_connection_runtime(...)` selects a runtime controller for the resolved `PrinterDevice`. If no controller is needed, it returns an empty context. If a controller is needed, it may attach to the connection, probe capabilities, run a handshake, or prepare notification state.

GATT write response is not a printer protocol ACK. If a family needs ACKs, status, or completion waits, model that as protocol steps and runtime controller behavior, not as transport adapter policy.

## Detecting Versus Discovering

Catalog detection and Bluetooth discovery are different concerns.

`PrinterCatalog.detect_device(...)` does not scan hardware. It maps a known name/address to a printable `PrinterDevice` when the catalog match is unambiguous.

`BluetoothDiscovery` scans hardware, asks `BluetoothEndpointResolver` to merge raw endpoints, then returns `PrinterDevice` objects for devices that can be printed automatically. UI/CLI scan views may use display helpers to include ambiguous or unsupported manual candidates.

Transport owns scanning mechanics. Devices own turning raw endpoints into logical printers.

## Paper And Media Boundaries

User-facing paper choices are catalog data. Profiles list exact paper preset keys; `PrintSettings.paper_preset_key` selects one for file printing.

Rendering uses the preset's render width. Protocol families receive low-level values they understand, such as left padding, maximum sheet height, or `paper_mode`. Transport does not receive media data.

`paper_mode` is a protocol recipe value. It must not become the GUI/CLI data source for paper selection.

Detailed paper preset data rules are in [catalog.md](catalog.md).

## Dependency Direction

Allowed direction is:

- `rendering -> raster`
- `devices -> protocol.family|protocol.types`
- `protocol -> raster`
- `printing -> devices`
- `printing -> rendering`
- `printing -> protocol`
- `printing -> transport`
- `transport -> devices`
- `transport -> protocol`

Practical rules:

- rendering should not depend on protocol builders
- protocol should not depend on transport
- protocol should not depend on printing runtime controllers
- devices should describe printers, not perform I/O
- transport should not know printer opcodes or family-specific ACK semantics

## Where To Put New Code

Put it in `devices` if it changes printer description, model detection, endpoint merging, profile loading, or config serialization.

Put it in `rendering` if it changes how files become pages or raster data.

Put it in `protocol` if it changes stateless packet building, compression, encoding, protocol variants, or command payloads.

Put it in `printing` if it changes file-to-job orchestration, print settings, diagnostics, connected-session behavior, send sequencing, or runtime controllers.

Put it in `transport` if it changes actual connection, scanning backend mechanics, characteristic selection, chunk writes, serial writes, or generic query/wait primitives.

If a change requires protocol-specific timing, ACK handling, or notification interpretation, it belongs in `printing.runtime` or protocol steps, not transport adapters.
