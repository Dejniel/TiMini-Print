from __future__ import annotations

from ...raster import PixelFormat, RasterBuffer
from ..compression import compress_zlib
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


def build_esc_star_24dot_raster(
    raster: RasterBuffer,
    *,
    band_trailer: bytes,
) -> bytes:
    """Pack a BW1 raster into ESC ``*`` 24-dot column bands."""

    raster.validate()
    if raster.pixel_format != PixelFormat.BW1:
        raise ValueError("ESC 24-dot raster packing requires a bw1 raster")
    width = raster.width
    payload = bytearray()
    for band_start in range(0, raster.height, 24):
        payload += b"\x1b\x2a\x21"
        payload += width.to_bytes(2, "little")
        for x in range(width):
            for stripe in range(3):
                value = 0
                for bit in range(8):
                    y = band_start + (stripe * 8) + bit
                    if y < raster.height and raster.pixels[(y * width) + x]:
                        value |= 1 << (7 - bit)
                payload.append(value)
        payload += band_trailer
    return bytes(payload)


def build_1f10_zlib_raster(
    raster: RasterBuffer,
    *,
    window_bits: int = 10,
) -> bytes:
    """Build a ``1f 10`` zlib-compressed one-bit raster frame."""

    width_bytes = packed_row_width_bytes(raster.width)
    height = raster.height
    if width_bytes > 0xFFFF or height > 0xFFFF:
        raise ValueError("1f 10 raster dimensions must fit in two bytes")

    compressed = compress_zlib(
        pack_bw1_rows(raster),
        window_bits=window_bits,
    )
    return (
        b"\x1f\x10"
        + width_bytes.to_bytes(2, "big")
        + height.to_bytes(2, "big")
        + len(compressed).to_bytes(4, "big")
        + compressed
    )


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
