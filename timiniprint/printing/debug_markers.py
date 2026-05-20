from __future__ import annotations

from ..raster import PixelFormat, RasterBuffer, RasterSet


def apply_debug_row_markers(raster_set: RasterSet, interval: int) -> RasterSet:
    """Add side ruler marks to a raster set without changing its dimensions."""
    if interval <= 0:
        raise ValueError("Debug row marker interval must be positive")
    return RasterSet(
        rasters={
            pixel_format: _mark_raster_rows(raster, interval)
            for pixel_format, raster in raster_set.rasters.items()
        }
    )


def _mark_raster_rows(raster: RasterBuffer, interval: int) -> RasterBuffer:
    pixels = list(raster.pixels)
    width = raster.width
    height = raster.height
    marker_value = _black_marker_value(raster.pixel_format)
    base_tick = min(8, max(4, width // 48))
    medium_tick = min(20, max(base_tick + 4, width // 24))
    long_tick = min(36, max(medium_tick + 8, width // 12))

    for y in range(height):
        row_start = y * width
        pixels[row_start] = marker_value
        pixels[row_start + width - 1] = marker_value
        if y % interval != 0:
            continue
        tick = base_tick
        if y % (interval * 10) == 0:
            tick = long_tick
        elif y % (interval * 5) == 0:
            tick = medium_tick
        _mark_row_edges(pixels, row_start, width, tick, marker_value)

    return RasterBuffer(
        pixels=pixels,
        width=width,
        pixel_format=raster.pixel_format,
    )


def _mark_row_edges(
    pixels: list[int],
    row_start: int,
    width: int,
    tick: int,
    marker_value: int,
) -> None:
    tick = min(tick, width)
    for x in range(tick):
        pixels[row_start + x] = marker_value
        pixels[row_start + width - 1 - x] = marker_value


def _black_marker_value(pixel_format: PixelFormat) -> int:
    if pixel_format == PixelFormat.GRAY8:
        return 0
    if pixel_format == PixelFormat.GRAY4:
        return 15
    return 1
