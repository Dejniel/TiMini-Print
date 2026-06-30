from __future__ import annotations

from dataclasses import dataclass

from ..devices.device import PrinterDevice
from ..devices.profiles import PaperPreset
from ..protocol.types import PaperMode
from .settings import PrintSettings


@dataclass(frozen=True)
class ResolvedPaper:
    key: str
    label: str
    paper_mode: PaperMode | None
    paper_width_px: int
    print_width_px: int
    render_width_px: int
    output_width_px: int | None = None
    protocol_left_padding_px: int = 0
    a4_sheet_max_height_px: int | None = None
    page_width_mm: int | None = None
    page_height_mm: int | None = None
    left_margin_px: int | None = None
    right_padding_px: int | None = None
    gap_mm: int | None = None
    alignment: str | None = None


def paper_presets_for_device(device: PrinterDevice | None) -> tuple[PaperPreset, ...]:
    if device is None:
        return ()
    return device.profile.paper_presets


def default_paper_preset_for_device(device: PrinterDevice | None) -> PaperPreset | None:
    presets = paper_presets_for_device(device)
    if device is None or not presets:
        return None
    if device.profile.default_paper_preset_key is not None:
        preset = _preset_by_key(presets, device.profile.default_paper_preset_key)
        if preset is not None:
            return preset
    return presets[0]


def resolve_paper(device: PrinterDevice, settings: PrintSettings) -> ResolvedPaper:
    preset = _selected_preset(device, settings)
    render_width = _normalized_width(preset.render_width_px)
    output_width = (
        None
        if preset.output_width_px is None
        else _normalized_width(preset.output_width_px)
    )
    return ResolvedPaper(
        key=preset.key,
        label=preset.label,
        paper_mode=preset.paper_mode,
        paper_width_px=_normalized_width(preset.paper_width_px),
        print_width_px=_normalized_width(preset.print_width_px),
        render_width_px=render_width,
        output_width_px=output_width,
        protocol_left_padding_px=preset.protocol_left_padding_px,
        a4_sheet_max_height_px=preset.a4_sheet_max_height_px,
        page_width_mm=preset.page_width_mm,
        page_height_mm=preset.page_height_mm,
        left_margin_px=preset.left_margin_px,
        right_padding_px=preset.right_padding_px,
        gap_mm=preset.gap_mm,
        alignment=preset.alignment,
    )


def _selected_preset(device: PrinterDevice, settings: PrintSettings) -> PaperPreset:
    presets = paper_presets_for_device(device)
    if settings.paper_preset_key:
        preset = _preset_by_key(presets, settings.paper_preset_key)
        if preset is None:
            raise ValueError(
                f"{device.display_name or device.profile_key} does not support paper "
                f"{settings.paper_preset_key!r}"
            )
        return preset
    default_preset = default_paper_preset_for_device(device)
    if default_preset is not None:
        return default_preset
    raise ValueError(f"{device.display_name or device.profile_key} does not define paper presets")


def _preset_by_key(presets: tuple[PaperPreset, ...], key: str) -> PaperPreset | None:
    return next((preset for preset in presets if preset.key == key), None)


def _normalized_width(width: int) -> int:
    return width if width % 8 == 0 else width - (width % 8)


__all__ = [
    "ResolvedPaper",
    "default_paper_preset_for_device",
    "paper_presets_for_device",
    "resolve_paper",
]
