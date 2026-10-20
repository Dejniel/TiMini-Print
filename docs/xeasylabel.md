# XEasyLabel HP6-C

Select model key `xeasylabel_hp6_c` for HP6-C from XEasyLabel
(`com.labelprinter.app`). It uses `yk_astra_p1`. Classic SPP is the documented
transport; use normal service/channel discovery.

Use the [standard printing API](protocol.md) and choose
`PrintSettings.paper_preset_key` from the device's paper presets. The default
is 15 mm continuous stock with an 88-dot content band inside the 96-dot head.
File printing applies the preset's 270-degree rotation automatically; do not
add a compensating rotation or mirror.

Gap-label presets use sensor positioning; do not select them for continuous
stock. For prepared raster input, follow the
[raster layout and chunking contract](protocol.md#build-from-raster-data).

Missing optional status replies do not prevent printing. Reported paper-out,
cover-open, low-battery, overheating or cutter-not-ready conditions raise
`PrinterNotReadyError`. Completion is awaited passively; a timeout leaves
physical completion unconfirmed. On-demand diagnostics expose `status`,
`battery`, `firmware` and `serial`.

The implementation is code-tested, not yet hardware-tested. Gap-stock operation
still needs physical verification.
