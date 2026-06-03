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
