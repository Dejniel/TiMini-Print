"""Resolved, user-facing print choices. Describing options never communicates."""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..devices import PrinterDevice
from ..devices.profiles import PaperPreset
from ..protocol import PrinterProtocol
from ..protocol.runtime import RuntimePrintCapabilities
from ..protocol.types import AntiCrease, PageConfirmationPolicy, PrintMaterialPreset
from .settings import ImageMode, PrintQuality, PrintSettings


@dataclass(frozen=True)
class PrintOptions:
    """Choices for one paper/image/quality/session context, not hardware probing.

    image_mode and print_quality identify the supported context used to resolve
    dependent choices. Unsupported old selections fall back in this snapshot
    only; caller-owned PrintSettings are never changed. Building a job retains
    its existing settings validation. Empty tuples mean no selectable control.
    Normalized blackening levels are 1..5, never native heating/density values.
    """

    paper_preset_key: str
    paper_presets: tuple[PaperPreset, ...]
    image_mode: ImageMode
    image_modes: tuple[ImageMode, ...]
    print_quality: PrintQuality
    print_qualities: tuple[PrintQuality, ...]
    blackening_levels: tuple[int, ...]
    text_mode_available: bool
    anti_crease_modes: tuple[AntiCrease, ...] = ()
    material_presets: tuple[PrintMaterialPreset, ...] = ()
    page_confirmation: PageConfirmationPolicy = PageConfirmationPolicy.UNAVAILABLE
    copies_available: bool = False
    copy_gap_available: bool = False


def describe_print_options(
    device: PrinterDevice, settings: PrintSettings | None = None, *,
    runtime_capabilities: RuntimePrintCapabilities | None = None,
) -> PrintOptions:
    """Describe offline profile choices, optionally refined by this session.

    Recompute after changing paper, image mode/codec or session. No printer
    detection, preparation, writes, file rendering or mutable caches occur.
    """
    requested = settings if settings is not None else PrintSettings()
    modes = requested.available_image_modes(device, runtime_capabilities=runtime_capabilities)
    if not modes:
        raise ValueError("The selected paper/codec has no implemented image mode")
    mode = ImageMode(requested.image_mode) if requested.image_mode in modes else modes[0]
    selected = replace(requested, image_mode=mode, print_quality=PrintQuality.STANDARD)
    qualities = (PrintQuality.STANDARD,)
    pipeline = selected.resolve_image_pipeline(device, runtime_capabilities=runtime_capabilities)
    protocol = PrinterProtocol(device)
    controls = protocol.supported_print_settings(
        paper_preset_key=selected.paper_preset_key, image_pipeline=pipeline,
        runtime_capabilities=runtime_capabilities,
    )
    paper = (device.profile.paper_preset(selected.paper_preset_key) if selected.paper_preset_key
             else device.profile.default_paper_preset)
    return PrintOptions(
        paper.key, tuple(device.profile.paper_presets), mode, modes,
        PrintQuality.STANDARD, qualities,
        tuple(range(1, 6)) if "blackening" in controls else (), "text_mode" in controls,
    )
