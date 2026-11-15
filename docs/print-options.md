# Print options API

`PrintSettings` holds requested values for one print job. `PrintOptions` is an
immutable description of the available choices for the selected paper, image
mode/codec and prepared session. Describing options performs no I/O: it does
not query the printer or read/write device configuration.

## Offline and connected use

```python
from timiniprint.printing import describe_print_options
from timiniprint.printing.settings import PrintSettings

settings = PrintSettings()
options = describe_print_options(device, settings)

# After connect_printer(), use its resolved device and session capabilities.
options = printer.print_options(settings)
for mode in options.image_modes:
    print(mode.value)
```

Both entry points use the same resolver and the existing protocol rules. The
connected form uses the prepared device, which may differ from the discovery
result. There is no separate options cache. Recompute after changing paper,
image mode/codec or session capabilities.

## Returned choices

The snapshot describes:

- `paper_preset_key` and `paper_presets`: the selected and available papers;
- `image_mode` and `image_modes`: the effective selection and available image
  conversions, preferred first;
- `print_quality` and `print_qualities`: only `STANDARD` in this edition;
- `blackening_levels`: normalized user levels 1..5 when the selected recipe
  has an adjustable control, otherwise an empty tuple;
- `text_mode_available`: whether the recipe exposes a text/image treatment
  control.

Optional fields describe no implemented control in this edition:
`anti_crease_modes` and `material_presets` are empty, `page_confirmation` is
`UNAVAILABLE`, and `copies_available` and `copy_gap_available` are false.
These fields do not enable new print recipes or printer commands.

Options follow the selected image pipeline rather than only the profile's
default encoding. Known negative session capabilities hide the affected
control, not unrelated options. Unknown capabilities are not known negatives.

## Validation and ownership

An unsupported image-mode selection falls back **in the description only**.
The caller's settings remain unchanged; building a print job retains its
existing validation. Invalid explicit paper or codec selections still raise
errors rather than silently selecting a different paper or protocol.

`print_capabilities()` remains the low-level session findings, not another
options resolver. `None` means no additional findings, not that the printer has
no supported features. No runtime controllers or transport adapters are changed
by describing options.
