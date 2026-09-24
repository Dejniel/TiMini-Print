# Protocol / Integration Guide

Start here if you want to use TiMini-Print from your own code. This document is intentionally about usage flow, not catalog internals.

The normal path is:

1. resolve a `PrinterDevice`
2. choose a connector
3. create a `ConnectedPrinter`
4. call `print_file(...)`, `print_text(...)`, `feed(...)`, `retract(...)`, or `send_job(...)`

For package boundaries, read [architecture.md](architecture.md). For profile/model JSON data, read [catalog.md](catalog.md).

## Mental Model

`PrinterDevice` describes one printer as TiMini intends to use it: selected profile, protocol family, protocol variant, image pipeline, runtime settings, paper presets, and optional transport target.

A connector opens a low-level transport connection for that device. The built-in connectors are:

- `BleakBluetoothConnector`
- `SerialConnector`

`connect_printer(device, connector, ...)` combines those pieces into a `ConnectedPrinter`. It opens the connection, prepares any runtime controller required by the selected printer family, and returns the object used by app-level code.

`ConnectedPrinter` owns the active session. Use it for:

- `print_file(...)` for `.png`, `.jpg`, `.pdf`, or `.txt`
- `print_text(...)` for raw text
- `send_job(...)` when you already built a `ProtocolJob`
- `feed()` and `retract()` for manual paper motion
- `disconnect()` or `async with` for connection cleanup

## Printer Conditions And Failed Operations

Blocking states reported by the printer raise
`timiniprint.printing.PrinterNotReadyError`. Its `reasons` tuple contains
`timiniprint.protocol.PrinterStatusCode` values: `paper_out`, `cover_open`,
`overheated`, `low_battery`, `busy`, `ribbon_error`, `cutter_error`, `not_ready`,
or `printer_error`. Use these stable codes for UI decisions and localization;
`detail` / `str(error)` preserves the family-specific diagnostic message.
An unspecified refusal is not guessed to mean paper out.

Catch this exception around connected print/send/paper-motion operations and
connection preparation. Present the condition as requiring printer attention,
not as a connection failure or program bug. GUI/CLI reporting uses the
`printer_not_ready` warning key and includes the string codes in `reasons`.
CLI still returns a nonzero exit status (2); it did not complete the operation.

A failed print stops subsequent pages/copies, does not automatically retry or
resume, and does not itself disconnect an established session. Some content may
already have printed. Resolve the condition and let the user decide what to
print next. Normal connection cleanup still applies: exiting a context or
failing initial preparation closes the acquired connection.

This does not make every status fatal: family-specific warnings stay warnings.
Missing/malformed replies and timeouts are never `PrinterNotReadyError`;
each family keeps its existing required/optional-reply policy. No new status
queries or retry policy are implied.

## Print A File Over Bluetooth

```python
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.settings import PrintSettings
from timiniprint.transport.bluetooth import BluetoothDiscovery, BleakBluetoothConnector

catalog = PrinterCatalog.load()
discovery = BluetoothDiscovery(catalog)

devices = await discovery.scan_devices()
if not devices:
    raise RuntimeError("No supported printers found")

device = devices[0]

async with await connect_printer(device, BleakBluetoothConnector()) as printer:
    await printer.print_file(
        "example.png",
        settings=PrintSettings(blackening=3),
    )
```

This path handles file conversion, rendering, protocol job building, protocol steps, runtime waits, stream chunking, and disconnect cleanup. The caller does not manually pass stream parameters or runtime controllers.

### Prepared sessions and low-level integration

`connect_printer(...)` returns only after identification and preparation have
completed. Use `printer.printer_device()` and `printer.print_capabilities()`
for paper choices and job building, not the discovery-time configuration.
These accessors perform no I/O. Model, geometry and protocol do not change
between jobs on that connection; reconnect performs preparation again.

An adapter which opens its own connection can use:

```python
from timiniprint.printing.connected import ConnectedPrinter
from timiniprint.printing.runtime import prepare_connection_runtime

prepared = await prepare_connection_runtime(selection, connection)
printer = ConnectedPrinter(connection, prepared)
```

The adapter owns cleanup if preparation raises, including cancellation.
Prefer `connect_printer(...)` when possible because it provides that cleanup.
`PreparedPrinter` contains one required `device`, optional `capabilities`, and
the selected `runtime_controller` (possibly `None` for a stateless protocol).
Do not assemble a prepared result around an uninitialized controller.

Low-level API migration:

- `PreparedRuntimeContext` is replaced by the complete `PreparedPrinter` result.
- `ConnectedPrinter` takes `(connection, prepared)`, without a separate device.
- `send_prepared_job` takes `(prepared, connection, job)` and never initializes a runtime.
- File/raster builders take `runtime_capabilities=prepared.capabilities`, not a runtime context.
- Custom controllers implement `prepare(device, session, timeout=...)` and return
  `PreparedPrinter`; separate probe/resolution/capability hooks and implicit
  `adopt_previous` state copying are removed.

The optional `controller=` argument to preparation/`connect_printer` supplies a
fresh bootstrap controller for that connection. A bootstrap selecting another
family must return that family's ready controller and negotiated state.
Connection bindings and stream settings must remain compatible. This is an
integration extension, not permission to probe unrelated Bluetooth devices.

For custom transports, `attach_runtime_controller` owns the attached controller's
lifecycle: detach and stop the previous controller in the event loop that owns
its tasks before installing the replacement. The replacement is already
prepared; do not copy old state or rerun initialization. Attaching `None`
stops and detaches the receiver. Connection initialization hooks belong to the
first attachment, not replacement. Disconnect must also stop the attached
controller and release the transport even if controller cleanup fails.

Expose `connection.active_ble_profile` as the GATT configuration applied to
the active BLE connection, or `None` for Classic/SPP or serial. Preparation
checks that configuration, not the list of discovery endpoints: a successful
SPP fallback is not constrained by an unused BLE profile. Without this
property, preparation rejects a change of BLE profile because the active
bindings cannot be verified.

Cancelling Bluetooth I/O waits for the current worker operation to finish or
reach its timeout before cleanup can close its socket/event loop. Cancellation
is therefore not necessarily immediate and does not undo bytes already sent.
Custom executor-backed transports must likewise settle worker I/O before
releasing resources; cancelling its asyncio future alone does not stop a thread.

### BLE Write Control

Custom BLE adapters must retain notification-channel identity. A profile's
`control_notify_char_uuid` is a separate flow-control channel: subscribe when
present and deliver its bytes to `RuntimeController.handle_control_notification`,
not `handle_notification` or printer-reply waiters. Report a successful
subscription through `session.can_observe_control_notifications()`. Failure
to subscribe to a present control characteristic must fail connection setup.
Notifications received before runtime attachment must retain their channel
during replay.

Immediately before **each physical GATT write**, after chunking, await
`controller.before_write(session, size=len(chunk), timeout=timeout)`.
This applies to payload, protocol-step, control-packet and bulk writes.
An exception stops sending; do not invent a credit, retry a failed write or
refund its permission. Credit interpretation belongs to the runtime, never
the adapter. The default hooks do nothing, preserving other families' behavior.
Classic/SPP writes do not use these BLE hooks.

## Choose A Bluetooth Printer

Use `scan_devices()` when you want printable devices that TiMini can resolve automatically.

```python
devices = await discovery.scan_devices()
```

Use `resolve_device(...)` when you want one specific discovered Bluetooth device by name or address.

```python
device = await discovery.resolve_device("AA:BB:CC:DD:EE:01")
device = await discovery.resolve_device("X6H-ABCD")
```

`BluetoothDiscovery` scans hardware. `PrinterCatalog.detect_device(...)` does not scan; it only maps a known advertised name/address to a `PrinterDevice`. Use catalog detection when another platform already scanned Bluetooth for you.

NIIMBOT D11 uses the runtime-selected `d11_auto` task. Connect first and use
`printer.printer_device()` for direct job building: negotiated versions 1/2 select
`d110`, and other or unavailable versions select `d11_v1`. D11S and the explicit
`niimbot_d11` profile keep the older task. `PrinterProtocol` alone cannot
negotiate an unresolved task.

## Known Model Or Serial Target

Use this path when Bluetooth discovery is not involved and you already know the model/profile.

```python
from timiniprint.devices import PrinterCatalog, SerialTarget
from timiniprint.printing.connected import connect_printer
from timiniprint.transport.serial import SerialConnector

catalog = PrinterCatalog.load()
device = catalog.device_from_profile(
    "a200",
    transport_target=SerialTarget("/dev/rfcomm0"),
)

async with await connect_printer(device, SerialConnector()) as printer:
    await printer.print_file("example.pdf")
```

For normal user-selected models prefer `device_from_model(...)` or an exported printer config. `device_from_profile(...)` is useful for diagnostics and low-level integration where you intentionally bypass model metadata.

## Print Text

```python
async with await connect_printer(device, BleakBluetoothConnector()) as printer:
    await printer.print_text("Hello from TiMini")
```

`print_text(...)` uses the same text converter and print pipeline as a temporary `.txt` file.

Long text is split into bounded raster chunks for rendering and preview, but those chunks form one continuous print flow. Intermediate chunks do not add the page-positioning commands used between PDF pages.

## Paper Choice

For high-level file printing, select paper through `PrintSettings.paper_preset_key`.

```python
from timiniprint.printing.settings import PrintSettings

settings = PrintSettings(paper_preset_key="plain_384r")
await printer.print_file("label.png", settings=settings)
```

The key must be one of the paper preset keys supported by the device profile. The preset controls render width and any protocol-side paper recipe. Do not pass low-level `paper_mode` from GUI, CLI, or normal file-printing code.

See [catalog.md](catalog.md) for the data model behind paper presets.

## Available Print Controls

Connect first, then query the resolved device and live capabilities. Do not
maintain a separate list of printer families in a GUI:

```python
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat

device = printer.printer_device()
capabilities = printer.print_capabilities()
protocol = PrinterProtocol(device)
paper_key = device.profile.default_paper_preset.key
formats = protocol.supported_pixel_formats(
    paper_preset_key=paper_key, runtime_capabilities=capabilities,
)
adjustments = protocol.supported_print_settings(
    paper_preset_key=paper_key, runtime_capabilities=capabilities,
)
can_feed = protocol.supports_paper_motion("feed")
can_retract = protocol.supports_paper_motion("retract")
```

`formats` describes input pixel formats, not user-facing codec names. Re-query
it when the selected paper changes. When the user chooses a format, pass it as
`pixel_format` to `supported_print_settings(...)` as well. The returned keys
are `blackening` and/or `text_mode` when those printer adjustments are
implemented. Rendering controls such as rotation, trimming and monochrome
dithering are separate; they do not require printer commands.

High-level printing uses a single `PrintSettings.image_mode`:
`grayscale`, `atkinson`, `floyd_steinberg`, `bayer_4`, `bayer_8` or
`threshold`. Omitting it prefers grayscale when available, otherwise Atkinson.
The GUI presents these in one **Dithering** list without an Automatic entry.
Grayscale does not run a monochrome dither algorithm.

```python
from timiniprint.printing.settings import ImageMode, PrintSettings

settings = PrintSettings(paper_preset_key=paper_key)
modes = settings.available_image_modes(device, runtime_capabilities=capabilities)
# Preferred choice is modes[0]. Keep a user's explicit choice while supported.
settings.image_mode = modes[0]
pipeline = settings.resolve_image_pipeline(device, runtime_capabilities=capabilities)
adjustments = protocol.supported_print_settings(
    paper_preset_key=paper_key, pixel_format=pipeline.default_format,
    runtime_capabilities=capabilities,
)
await printer.print_file("photo.png", settings=settings)
```

Recompute available modes after connection preparation and paper changes. Pass
the prepared capabilities to previews and job building too. Unknown capabilities
mean catalog-only choices; recipes requiring negotiated data still require a
prepared session. An explicit unsupported mode raises `ValueError`; an existing
runtime grayscale rejection keeps the protocol's monochrome fallback.

Integration migration: replace `PrintSettings.dither_mode` with `image_mode`
(`none` becomes `threshold`), and replace `PrintSettings.pixel_format_override`
with `image_mode="grayscale"` or a monochrome algorithm. There are no constructor
aliases. Low-level `DitherMode`, `PixelFormat` and `PrinterProtocol` format/codec
parameters are unchanged. `image_encoding_override` remains an expert codec
constraint, not another user-facing selector.

Prepared-raster helpers choose only among supplied raster formats; they never
convert BW1 into grayscale or apply dithering again. A rendered page's explicit
pipeline remains authoritative when its protocol job is built.

The CLI exposes the same values as `--image-mode`, e.g.
`--image-mode grayscale` or `--image-mode atkinson`.

These capability methods do not send anything. `print_capabilities()` returns
information already collected by the live session; it replaces the former
`raster_capabilities()` method. Integrations must update that call; there is no
compatibility alias. A `None` result means no additional runtime restrictions
were reported, not that the printer has no capabilities.

## Build A Job Without Sending

Use `PrintJobBuilder` only when you want to build file-based `ProtocolJob` objects yourself.

```python
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.settings import PrintSettings

builder = PrintJobBuilder(device, settings=PrintSettings(blackening=4))
job = builder.build_from_file("example.png")
```

Use `iter_page_jobs(...)` when memory matters and you want one page at a time.

```python
for page in builder.iter_page_jobs("document.pdf"):
    await printer.send_job(page.job)
```

If you later send these jobs through TiMini transport, use `ConnectedPrinter.send_job(...)` so runtime completion waits and step execution are applied. Built-in low-level connections accept only stream-only jobs directly.

## Build From Raster Data

Use `PrinterProtocol` when you already have raster data and do not want the repo file/rendering pipeline.

```python
from timiniprint.protocol import PageFlow, PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet

raster = RasterBuffer(
    pixels=[1] * 64,
    width=8,
    pixel_format=PixelFormat.BW1,
)
raster_set = RasterSet.from_single(raster)

job = PrinterProtocol(device).build_job(
    raster_set,
    is_text=False,
    blackening=3,
)
```

`PrinterProtocol` is stateless packet building. It does not connect to hardware and does not create runtime controllers.

When an integration splits one continuous raster document into several calls, pass `page_index`, `page_count`, and `page_flow=PageFlow.CONTINUOUS` for every chunk. The default is `PageFlow.PAGED`, so existing one-page raster calls remain self-contained.

### Phomemo Sessions

Connection preparation reads device information through the connection's
query/reply interface; the runtime does not select or require a particular
transport. Missing optional firmware, battery or feature replies do not block
printing. Recipes that need a serial number for raster selection require its
reply before preparation completes.

Pass `printer.print_capabilities()` to builders as `runtime_capabilities`, just
as for other prepared printers. The snapshot is immutable; later status
notifications update runtime diagnostics, not an already prepared job.
Reported grayscale or double-DPI flags do not automatically enable an encoder.
Print Master recipes have their own reply format and do not use Phomemo startup.

### Phomemo Compact Raster Input

Phomemo M02/T02 accept up to 384 content dots; M02S/M02 Pro accept up to
576 at 300 dpi. Pass the content raster without the four blank top rows:
the encoder adds them. M02S/M02 Pro label rolls are first padded on the left
to their respective wire widths of 588/583 dots.
Partial bytes are zero-filled without resizing or reversing the image.

These profiles use compact density levels 1..4, mapped to wire density and
its coefficient. Their low/middle/high defaults select levels 1/2/4. Setup
occurs on the first page and two separate feed packets finish the document.
Do not append the alternative `1F F0` footer or initialize each continuous
render chunk independently.

## Custom Connector

A custom connector lets you reuse TiMini protocol logic without using the built-in Bluetooth stack. It must connect using a `PrinterDevice` and return a connection with `send(job)` and `disconnect()`. The basic `send(job)` operation is the stream-only fallback used when a job has no execution steps.

```python
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.connected import connect_printer


class MyConnection:
    def __init__(self, raw_link, device):
        self._raw_link = raw_link
        self._device = device

    async def send(self, job):
        await send_payload_over_my_link(
            self._raw_link,
            payload=job.payload,
            chunk_size=self._device.profile.stream.chunk_size,
            delay_ms=self._device.profile.stream.delay_ms,
        )

    async def disconnect(self):
        await self._raw_link.close()


class MyConnector:
    async def connect(self, device):
        raw_link = await open_my_link(device.transport_target)
        return MyConnection(raw_link, device)


catalog = PrinterCatalog.load()
device = catalog.device_from_profile("x6h")

async with await connect_printer(device, MyConnector()) as printer:
    await printer.print_file("example.png")
```

For full support of runtime-sensitive families, the connection may also implement optional methods from `RuntimeProbeConnection` in `timiniprint.transport.base`, such as `send_control_packet(...)`, `query_control_packet(...)`, `wait_for_reply(...)`, `wait_for_notification(...)`, and `send_control_packet_wait_notification(...)`. If they are missing, runtime-sensitive families degrade or fail according to their controller.

`wait_for_reply(...)` is the transport-neutral passive receive operation used by protocol steps and completion controllers. A Classic SPP connection reads from its socket; a BLE connection can satisfy the same operation through notifications. `wait_for_notification(...)` remains available for controllers whose behavior is specifically tied to BLE notification state.

Deliver received bytes to an attached runtime controller before evaluating
pending reply predicates, so they can use the controller's updated state.
BLE connectors should honor `device.ble_transport_profile.notify_service_uuids`
when nonempty: subscribe to every notify/indicate characteristic in those
services, not just the profile's primary notification UUID.

`ProtocolStep.query(...)` and `ProtocolStep.wait(...)` can set
`reply_required=True`. A missing or rejected reply then fails the job before
the next step; repeated queries first use their configured polling budget.
Such jobs cannot fall back to unchecked stream-only sending. NIIMBOT requires
setup, page and print-end acknowledgements as well as its task-specific
completion confirmation. Its runtime retains early page-index notifications,
reassembles fragmented replies and reports printer faults separately from
transport errors. Other families retain their existing required/optional policy.

Luck normal/A4 jobs require density acknowledgement, a usable status reply and
confirmed finalization. Control queries wait up to 3 seconds; finalization
waits up to 70 seconds, or 120 seconds for Lujiang A4. These are response
timeouts, not fixed delays. Optional paper-setting replies retain their wait
without aborting the job on a missing ACK; A2/A2H paper selection does not read
a reply. Keep the full `ProtocolJob.steps`: sending only its payload bypasses
these transaction rules. Reported printer faults raise `PrinterNotReadyError`;
missing required replies raise `ProtocolReplyError`.
Normal status faults preserve all reported conditions, including both
overheat bits. Charging and the unused high bit do not block a new job.

Luck normal presets include black-mark and tattoo media. Black-mark positioning
keeps the selected variant's paper command and page-marker policy; tattoo uses
paper `0x40`, the profile's trailing feed and no label markers. D80 tattoo uses
the local `0x40` recipe without serial-range heating overrides. These media use
the same raster encoders and reply contracts as the existing jobs.

LuckP A41 reads firmware during connection preparation. Normalized versions
below `1.26` use density `0..2` (default 1); versions at or above it use
`1..15` (default 8) and speed `0..8` (default 4). The firmware gate compares
text, not semantic versions. No reply keeps the initial density `0..15`
(default 7) with speed disabled and emits a warning. Build jobs from the
returned `PreparedPrinter.device` to use these negotiated defaults. An enabled
speed command requires its own `OK`-prefix acknowledgement. A42 does not probe
firmware and remains at density `0..2` without a speed command.

## Editable Printer Configs

Use printer configs when you want an explicit, editable runtime device instead of auto-detection every time.

```python
from pathlib import Path
import json

from timiniprint.devices import PrinterCatalog

catalog = PrinterCatalog.load()
device = catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:59")
if device is None:
    raise RuntimeError("Printer profile not detected")

printer_config = catalog.serialize_printer_config(device)
Path("printer.json").write_text(
    json.dumps(printer_config, indent=2) + "\n",
    encoding="utf-8",
)

loaded = json.loads(Path("printer.json").read_text(encoding="utf-8"))
manual_device = catalog.device_from_printer_config(loaded)
```

Model-based configs keep `model_key` as the fallback, so deleting an override falls back to the catalog model. Raw profile-based configs are possible for diagnostics, but they do not carry model detection metadata.

## Debug A Protocol Job

Use the tool version when you need to compare packet structure or image encoding without connecting to hardware.

```bash
python3 tools/debug_protocol_job.py --model mx10 --text "test" --out job.json
python3 tools/debug_protocol_job.py --runtime-preset mx06 --text "test" --image-encoding v5g_gray --out job.json
```

`--profile` and `--runtime-preset` intentionally use internal catalog keys. Prefer `--model` or `--printer-config` unless you are debugging catalog internals.
