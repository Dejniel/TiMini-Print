from __future__ import annotations

from ...raster import PixelFormat, RasterBuffer
from ..encoding import pack_line


def pack_bw1_rows(raster: RasterBuffer, *, lsb_first: bool = False) -> bytes:
    raster.validate()
    if raster.pixel_format != PixelFormat.BW1:
        raise ValueError("BW1 raster packing requires a bw1 raster")

    payload = bytearray()
    for row in range(raster.height):
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        payload += pack_line(list(line), lsb_first=lsb_first)
    return bytes(payload)


def packed_row_width_bytes(width: int) -> int:
    if width <= 0:
        raise ValueError("Raster width must be greater than zero")
    return (width + 7) // 8


def build_gs_v0_blocks(
    raster: RasterBuffer,
    *,
    max_lines_per_block: int = 0xFF,
    lsb_first: bool = False,
    mode: int = 0,
) -> bytes:
    raster.validate()
    if max_lines_per_block <= 0:
        raise ValueError("max_lines_per_block must be greater than zero")
    if mode < 0 or mode > 0xFF:
        raise ValueError("GS v 0 mode must fit in one byte")

    width_bytes = packed_row_width_bytes(raster.width)
    height = raster.height
    payload = bytearray()
    line = 0
    while line < height:
        lines = min(max_lines_per_block, height - line)
        block = raster.slice_rows(line, lines)
        payload += b"\x1d\x76\x30"
        payload += bytes([mode])
        payload += width_bytes.to_bytes(2, "little")
        payload += lines.to_bytes(2, "little")
        payload += pack_bw1_rows(block, lsb_first=lsb_first)
        line += lines
    return bytes(payload)
