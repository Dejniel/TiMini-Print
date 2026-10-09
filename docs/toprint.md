# ToPrint integration

ToPrint has two distinct job dialects. P1 uses `toprint_tspl`; P2/P5, P11 and
YHK use `toprint_hprt_esc`. Select the source/model explicitly when a Bluetooth
name also belongs to another application. Classic SPP is preferred.

P11 has narrow label and black-mark presets with an 88-dot working raster.
YHK has a continuous 376-dot preset. These are working print formats, not
confirmed head-width limits. P1 and P2/P5 keep the existing 384-dot default;
their hardware maxima are not established by the recovered recipe. Paper
geometry remains profile data. No editor scaling, margins or forced rotations
are imposed on user content.

Raw monochrome is the default. Optional compression uses the existing encoding
override, with no extra setting or protocol variant:

```python
settings = PrintSettings(image_encoding_override=ImageEncoding.TOPRINT_TSPL_ZLIB)
# ESC devices instead use ImageEncoding.TOPRINT_HPRT_ESC_ZLIB.
await printer.print_file("label.png", settings=settings)
```

Import `PrintSettings` from `timiniprint.printing.settings` and `ImageEncoding` from
`timiniprint.protocol`. The TSPL encoding keeps the complete zlib stream;
the ESC encoding removes its two-byte header but retains the checksum.
Neither encoding is interchangeable with Eleph Label's compressed framing.
Compression is covered by code tests, not hardware verification.

Unsolicited status replies are observed without sending queries. Paper-out,
cover-open and the documented composite low-battery condition raise
`PrinterNotReadyError` during an affected attempt. Resolve the condition before
starting another attempt; failed jobs are not automatically replayed. Printing
and paused flags are logged but do not block the writer or prove completion.
Successful sending alone does not confirm physical print completion.

GT08/GW08 have no recovered ToPrint A4 sender, and P3 alone has no independent
ToPrint discovery route. They do not select these recipes automatically.
Supported same-name variants from other source applications remain separate.
