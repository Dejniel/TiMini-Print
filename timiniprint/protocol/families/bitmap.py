from __future__ import annotations

from ...raster import PixelFormat, RasterBuffer
from ..compression import compress_zlib
from ..encoding import pack_line


def pad_raster(
    raster: RasterBuffer, *, left: int = 0, right: int = 0,
    top: int = 0, bottom: int = 0, fill: int = 0,
) -> RasterBuffer:
    """Add constant-value margins required by a wire raster layout."""
    if min(left, right, top, bottom) < 0:
        raise ValueError("Raster padding must be non-negative")
    if not (left or right or top or bottom):
        return raster
    width = left + raster.width + right
    pixels: list[int] = [fill] * (width * top)
    for row in range(raster.height):
        start = row * raster.width
        pixels.extend([fill] * left)
        pixels.extend(raster.pixels[start:start + raster.width])
        pixels.extend([fill] * right)
    pixels.extend([fill] * (width * bottom))
    return RasterBuffer(pixels, width, raster.pixel_format)


def pack_bw1_rows(
    raster: RasterBuffer,
    *,
    lsb_first: bool = False,
    reverse_traversal: bool = False,
) -> bytes:
    """Pack BW1 rows, optionally walking from bottom-right to top-left."""

    raster.validate()
    if raster.pixel_format != PixelFormat.BW1:
        raise ValueError("BW1 raster packing requires a bw1 raster")

    payload = bytearray()
    rows = (
        range(raster.height - 1, -1, -1)
        if reverse_traversal
        else range(raster.height)
    )
    for row in rows:
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        if reverse_traversal:
            line = line[::-1]
        payload += pack_line(list(line), lsb_first=lsb_first)
    return bytes(payload)


def packed_row_width_bytes(width: int) -> int:
    if width <= 0:
        raise ValueError("Raster width must be greater than zero")
    return (width + 7) // 8


def build_esc_star_raster(
    raster: RasterBuffer,
    *,
    band_trailer: bytes,
    mode: int = 33,
) -> bytes:
    """Pack BW1 columns using ESC ``*`` modes 0/1 (8 dots) or 32/33 (24)."""

    raster.validate()
    if raster.pixel_format != PixelFormat.BW1:
        raise ValueError("ESC * raster packing requires a bw1 raster")
    if mode not in (0, 1, 32, 33):
        raise ValueError("ESC * mode must be 0, 1, 32 or 33")
    width = raster.width
    height = raster.height
    if width > 0xFFFF:
        raise ValueError("ESC * width must fit in two bytes")
    stripes = 1 if mode < 32 else 3
    band_height = stripes * 8
    payload = bytearray()
    for band_start in range(0, height, band_height):
        payload += b"\x1b\x2a" + bytes([mode])
        payload += width.to_bytes(2, "little")
        for x in range(width):
            for stripe in range(stripes):
                value = 0
                for bit in range(8):
                    y = band_start + (stripe * 8) + bit
                    if y < height and raster.pixels[(y * width) + x]:
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
    command: int = 0x76,
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
        payload += bytes((0x1D, command, 0x30))
        payload += bytes([mode])
        payload += width_bytes.to_bytes(2, "little")
        payload += lines.to_bytes(2, "little")
        payload += pack_bw1_rows(block, lsb_first=lsb_first)
        line += lines
    return bytes(payload)
