from __future__ import annotations

from dataclasses import dataclass

from ..devices.device import PrinterDevice
from ..devices.profiles import PaperPreset
from ..protocol.types import PaperMode
from ..raster import DitherMode, PixelFormat, RasterBuffer, RasterSet
from .settings import PrintSettings


@dataclass(frozen=True)
class ResolvedPaper:
    key: str
    label: str
    paper_mode: PaperMode | None
    paper_width_px: int
    render_width_px: int
    render_height_px: int | None = None
    left_padding_px: int = 0
    top_padding_px: int = 0
    max_height_px: int | None = None
    raster_height_px: int | None = None
    mirror_horizontal: bool = False
    dither_mode: DitherMode | None = None
    render_height_scale: float = 1.0


@dataclass(frozen=True)
class _PaperLayoutPlan:
    width: int
    height: int
    left_padding: int
    right_padding: int
    top_padding: int
    bottom_padding: int


def paper_presets_for_device(device: PrinterDevice | None) -> tuple[PaperPreset, ...]:
    if device is None:
        return ()
    return device.profile.paper_presets


def default_paper_preset_for_device(device: PrinterDevice | None) -> PaperPreset | None:
    if device is None:
        return None
    return device.profile.default_paper_preset


def resolve_paper(device: PrinterDevice, settings: PrintSettings) -> ResolvedPaper:
    preset = _selected_preset(device, settings)
    return ResolvedPaper(
        key=preset.key,
        label=preset.label,
        paper_mode=preset.paper_mode,
        paper_width_px=preset.paper_width_px,
        render_width_px=preset.render_width_px,
        render_height_px=preset.render_height_px,
        left_padding_px=preset.left_padding_px,
        top_padding_px=preset.top_padding_px,
        max_height_px=preset.max_height_px,
        raster_height_px=preset.raster_height_px,
        mirror_horizontal=preset.mirror_horizontal,
        dither_mode=preset.dither_mode,
        render_height_scale=preset.render_height_scale,
    )


def _selected_preset(device: PrinterDevice, settings: PrintSettings) -> PaperPreset:
    presets = paper_presets_for_device(device)
    if settings.paper_preset_key:
        preset = _preset_by_key(presets, settings.paper_preset_key)
        if preset is None:
            raise ValueError(
                f"{device.display_name or device.profile_key} does not support paper "
                f"{settings.paper_preset_key!r}."
                f"{_available_paper_presets_message(presets)}"
            )
        return preset
    default_preset = default_paper_preset_for_device(device)
    if default_preset is not None:
        return default_preset
    raise ValueError(f"{device.display_name or device.profile_key} does not define paper presets")


def _preset_by_key(presets: tuple[PaperPreset, ...], key: str) -> PaperPreset | None:
    return next((preset for preset in presets if preset.key == key), None)


def _available_paper_presets_message(presets: tuple[PaperPreset, ...]) -> str:
    if not presets:
        return ""
    lines = ["", "Available paper presets:"]
    lines.extend(f"  {preset.key} - {preset.label}" for preset in presets)
    return "\n".join(lines)


def _plan_paper_layout(
    paper: ResolvedPaper,
    *,
    content_width: int,
    content_height: int,
    minimum_width: int,
    content_box_height: int | None = None,
    content_label: str = "Content",
) -> _PaperLayoutPlan:
    if (
        paper.raster_height_px is not None
        and paper.top_padding_px + content_height > paper.raster_height_px
    ):
        if not paper.top_padding_px:
            raise ValueError(
                f"{content_label} height {content_height}px exceeds paper raster height "
                f"{paper.raster_height_px}px"
            )
        raise ValueError(
            f"{content_label} height {content_height}px plus top padding "
            f"{paper.top_padding_px}px exceeds paper raster height "
            f"{paper.raster_height_px}px"
        )
    final_width = max(content_width, minimum_width)
    left_padding = (final_width - content_width) // 2
    right_padding = final_width - content_width - left_padding
    reserved_height = max(content_height, content_box_height or content_height)
    final_height = (
        paper.raster_height_px
        if paper.raster_height_px is not None
        else paper.top_padding_px + reserved_height
    )
    return _PaperLayoutPlan(
        width=final_width,
        height=final_height,
        left_padding=left_padding,
        right_padding=right_padding,
        top_padding=paper.top_padding_px,
        bottom_padding=final_height - paper.top_padding_px - content_height,
    )


def apply_paper_layout_to_raster_set(raster_set: RasterSet, paper: ResolvedPaper) -> RasterSet:
    layout = _plan_paper_layout(
        paper,
        content_width=raster_set.width,
        content_height=raster_set.height,
        minimum_width=(
            raster_set.width if paper.left_padding_px else paper.paper_width_px
        ),
        content_label="Raster",
    )
    if (
        (layout.width, layout.height) == (raster_set.width, raster_set.height)
        and not paper.mirror_horizontal
    ):
        return raster_set
    return RasterSet(
        rasters={
            pixel_format: _apply_raster_layout(
                _pad_raster_buffer(
                    raster,
                    layout.left_padding,
                    layout.right_padding,
                    layout.top_padding,
                    layout.bottom_padding,
                ),
                mirror_horizontal=paper.mirror_horizontal,
            )
            for pixel_format, raster in raster_set.rasters.items()
        }
    )


def _pad_raster_buffer(
    raster: RasterBuffer,
    left_padding: int,
    right_padding: int,
    top_padding: int,
    bottom_padding: int,
) -> RasterBuffer:
    white = _white_pixel_for_format(raster.pixel_format)
    final_width = raster.width + left_padding + right_padding
    padded: list[int] = [white] * (final_width * top_padding)
    for row in range(raster.height):
        start = row * raster.width
        padded.extend([white] * left_padding)
        padded.extend(raster.pixels[start : start + raster.width])
        padded.extend([white] * right_padding)
    padded.extend([white] * (final_width * bottom_padding))
    return RasterBuffer(
        pixels=padded,
        width=final_width,
        pixel_format=raster.pixel_format,
    )


def _apply_raster_layout(
    raster: RasterBuffer,
    *,
    mirror_horizontal: bool,
) -> RasterBuffer:
    if not mirror_horizontal:
        return raster
    mirrored: list[int] = []
    for row in range(raster.height):
        start = row * raster.width
        mirrored.extend(reversed(raster.pixels[start : start + raster.width]))
    return RasterBuffer(
        pixels=mirrored,
        width=raster.width,
        pixel_format=raster.pixel_format,
    )


def _white_pixel_for_format(pixel_format: PixelFormat) -> int:
    if pixel_format == PixelFormat.GRAY8:
        return 255
    return 0


__all__ = [
    "ResolvedPaper",
    "apply_paper_layout_to_raster_set",
    "default_paper_preset_for_device",
    "paper_presets_for_device",
    "resolve_paper",
]
