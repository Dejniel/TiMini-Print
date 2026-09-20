# Architecture

Read [protocol.md](protocol.md) first if you want to use TiMini-Print from your own code. Read [catalog.md](catalog.md) for profile/model JSON data. This document is about package boundaries and where code belongs.

## Runtime Flow

The app-level flow is:

1. `devices` resolves an initial `PrinterDevice`
2. `transport` opens a connector-specific connection
3. `printing.connected.connect_printer(...)` prepares the complete configuration and runtime before publishing a connected printer
4. `ConnectedPrinter` prints files/text or sends prepared jobs
5. `protocol` builds packet payloads and optional protocol steps
6. `transport` writes bytes and exposes generic query/wait primitives

`PrinterDevice` is an immutable configuration, not a live connection. A catalog
selection may still need identification. `RuntimeController.prepare(...)`
returns one `PreparedPrinter`: the final device, its negotiated capability
snapshot, and the controller owning the session state. `ConnectedPrinter`
stores this result, not a second independently supplied device description.

Model, geometry, and protocol selection are parts of the same preparation.
A bootstrap may select a different family, but must return its prepared
controller and explicitly transfer any necessary negotiated state. Preparation
checks that the selected configuration still fits the open transport. It never
silently reconnects or changes GATT bindings. Failed or cancelled preparation
closes the acquired connection before exposing a printable object.

Preparation keeps protocol queries separate from configuration assembly.
Once a family has selected and validated its recipe, use
`selected.resolve_for_connection(original, refine_profile=...)` to assemble the
device without I/O. The selected recipe owns identity, protocol, raster and
runtime settings. User profile overrides survive only for the same profile
key; the optional pure profile refinement then applies confirmed hardware
constraints. The original connection address, stream settings and MTU request
are retained. This does not bypass preparation's GATT or controller checks.
Manual recipe validation and any conditional queries remain family-specific.

Sending reuses that controller for all pages, copies, and subsequent jobs. It
does not construct controllers or copy state from a previous controller.
Reconnect starts a fresh preparation. Live status and flow control remain
mutable controller state; the published print configuration remains immutable.

The file/raster builders take a device and optional capability data, never a
live controller. Known configurations can still build jobs offline. Printer
identity aliases are matched by the catalog independently of Bluetooth names;
unknown or ambiguous identities are not resolved by catalog order.

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
The high-level object for an active printer session. It owns an active connection and its `PreparedPrinter` result, then exposes `print_file(...)`, `print_text(...)`, `send_job(...)`, `feed()`, `retract()`, and `disconnect()`.

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

`RuntimeController.job_scope(...)` brackets one send and its completion wait.
It arms job-local reply state before any bytes are sent and releases it on
success, error or cancellation. Payload, named-step, controller-handled and
stream-fallback paths all use the same scope; transport does not own this state.

`prepare_connection_runtime(...)` selects a runtime controller for the initial `PrinterDevice`, or uses an explicitly supplied bootstrap. Without a controller, the result still contains the final device. With a controller, its `prepare(...)` resolves identity, geometry, capabilities and session state in one operation.

Preparation may select another protocol family only with its ready runtime (or no controller for a stateless recipe). It must retain the transport target, SPP/BLE policy, stream settings and BLE MTU request. An active BLE connection must retain its applied GATT profile, exposed by `connection.active_ble_profile`; an SPP/serial connection reports `None`. This is based on the transport that connected, including fallback, not the discovery candidates. Custom connections without that metadata cannot select a different BLE profile. A different active transport setup requires a separate connection, not an in-place mutation of the session.

The adapter owns the lifecycle of an attached controller: it stops the old
receiver in its owning event loop before replacement or disconnect. A prepared
replacement is installed without repeating initialization or copying runtime
state. Executor-backed I/O must finish before closing the socket and its loop,
including after caller cancellation. These are transport lifecycle rules, not
printer-family policy.

GATT write response is not a printer protocol ACK. If a family needs ACKs, status, or completion waits, model that as protocol steps and runtime controller behavior, not as transport adapter policy.

BLE profiles may opt into `notify_service_uuids`: the adapter subscribes to all
notify/indicate characteristics in those services and cleans up each successful
subscription. Empty service selection preserves the existing characteristic
selection. The transport selects endpoints by GATT metadata, never by opcodes.
On receipt, runtime observers update session state before pending reply
predicates run; this allows passive waits to depend on parsed runtime state.

## Detecting Versus Discovering

Catalog detection and Bluetooth discovery are different concerns.

`PrinterCatalog.detect_device(...)` does not scan hardware. It maps a known name/address to a printable `PrinterDevice` when the catalog match is unambiguous.

`BluetoothDiscovery` scans hardware, asks `BluetoothEndpointResolver` to merge raw endpoints, then returns `PrinterDevice` objects for devices that can be printed automatically. UI/CLI scan views may use display helpers to include ambiguous or unsupported manual candidates.

Transport owns scanning mechanics. Devices own turning raw endpoints into logical printers.

## Paper And Media Boundaries

User-facing paper choices are catalog data. Profiles list exact paper preset keys; `PrintSettings.paper_preset_key` selects one for file printing.

Rendering uses the preset's render width, optional fixed content height, leading
padding, and orientation transform. The printing layer applies the equivalent
layout to callers that provide an already prepared raster. Final paper/canvas
padding can include an exact raster height. Protocol families receive only
low-level values they understand, such as left padding, maximum sheet height,
or `paper_mode`. Transport does not receive media data.

`paper_mode` is a protocol recipe value. It must not become the GUI/CLI data source for paper selection.

`PrinterProtocol` resolves editable profile values before dispatching a
`PrintJobRequest`. The request carries the selected text/image `energy`,
the image-only `image_energy` at the same density, and `back_paper_num`
for recipes that need a positioning offset. Family builders must not maintain
a second model-specific table for these profile values.

Detailed paper preset data rules are in [catalog.md](catalog.md).

## Dependency Direction

Allowed direction is:

- `rendering -> raster`
- `devices -> raster`
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
