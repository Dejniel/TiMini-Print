"""Eleph-label P1 TSPL commands."""

from __future__ import annotations

from ....raster import PixelFormat
from ...compression import compress_zlib
from ...types import ImageEncoding
from .._tspl import bitmap_command, command, command_head
from ..base import PrintJobRequest
from ..bitmap import pack_bw1_rows, packed_row_width_bytes


_LINE_END = b"\n"


def build_p1_job(request: PrintJobRequest) -> bytes:
    if request.protocol_variant not in (None, "p1"):
        raise ValueError(
            f"Unsupported Eleph-label TSPL protocol variant: {request.protocol_variant}"
        )
    raster = request.require_raster(PixelFormat.BW1)
    raster.validate()

    job = bytearray()
    job += command(
        "SIZE",
        f"{_px_to_whole_mm(raster.width, request.dev_dpi)} mm,"
        f"{_px_to_whole_mm(raster.height, request.dev_dpi)} mm",
        line_end=_LINE_END,
    )
    if request.speed is not None:
        job += command("SPEED", f"{request.speed:02x}", line_end=_LINE_END)
    if request.density is not None:
        job += command("DENSITY", str(request.density), line_end=_LINE_END)
    job += command("CLS", line_end=_LINE_END)
    job += command("DIRECTION", "0", line_end=_LINE_END)
    if request.image_pipeline.encoding is ImageEncoding.ELEPH_TSPL_ZLIB:
        # Mode 3 uses black=1, unlike mode 0's white=1, and includes a byte length.
        compressed = compress_zlib(pack_bw1_rows(raster, lsb_first=False), window_bits=15)
        job += command_head(
            "BITMAP",
            f"0,0,{packed_row_width_bytes(raster.width)},{raster.height},3,{len(compressed)},",
        ) + compressed + _LINE_END
    else:
        job += bitmap_command(raster, line_end=_LINE_END, invert_bits=True)
    job += command("PRINT", "1,1", line_end=_LINE_END)
    return bytes(job)


def _px_to_whole_mm(value: int, dpi: int) -> int:
    # Geometry uses integer 8/12/24 dots per mm; rounded DPI must not lose a mm.
    dots_per_mm = round(dpi / 25.4)
    if dots_per_mm not in (8, 12, 24):
        raise ValueError("Eleph geometry requires 8, 12 or 24 dots/mm")
    return value // dots_per_mm
