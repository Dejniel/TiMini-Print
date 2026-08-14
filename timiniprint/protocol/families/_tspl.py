"""Shared TSPL command and uncompressed bitmap encoding."""

from __future__ import annotations

from ...raster import PixelFormat, RasterBuffer
from .bitmap import pack_bw1_rows, packed_row_width_bytes


def command(name: str, value: str | None = None, *, line_end: bytes) -> bytes:
    return command_head(name, value) + line_end


def command_head(name: str, value: str | None = None) -> bytes:
    if value is None:
        return name.encode("ascii")
    return f"{name} {value}".encode("ascii")


def bitmap_command(
    raster: RasterBuffer,
    *,
    line_end: bytes,
    invert_bits: bool = False,
) -> bytes:
    raster.validate()
    if raster.pixel_format is not PixelFormat.BW1:
        raise ValueError("TSPL bitmap jobs require a bw1 raster")
    if raster.width % 8 != 0:
        raise ValueError("TSPL bitmap jobs require width divisible by 8")

    packed = pack_bw1_rows(raster, lsb_first=False)
    if invert_bits:
        packed = bytes(value ^ 0xFF for value in packed)
    return (
        command_head(
            "BITMAP",
            f"0,0,{packed_row_width_bytes(raster.width)},{raster.height},0,",
        )
        + packed
        + line_end
    )
