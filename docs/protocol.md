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

This path handles file conversion, rendering, protocol job building, protocol steps, runtime waits, stream chunking, and disconnect cleanup. The caller does not manually pass `chunk_size`, `delay_ms`, `runtime_context`, or `runtime_controller`.

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

For full support of runtime-sensitive families, the connection may also implement optional methods from `RuntimeProbeConnection` in `timiniprint.transport.base`, such as `send_control_packet(...)`, `query_control_packet(...)`, `wait_for_notification(...)`, and `send_control_packet_wait_notification(...)`. If they are missing, runtime-sensitive families degrade or fail according to their controller.

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
