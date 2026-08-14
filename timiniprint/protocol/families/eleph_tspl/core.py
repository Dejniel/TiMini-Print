"""Eleph-label P1 TSPL commands."""

from __future__ import annotations

from ....raster import PixelFormat
from .._tspl import bitmap_command, command
from ..base import PrintJobRequest


_LINE_END = b"\r\n"


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
    job += command("GAP", "2 mm,0 mm", line_end=_LINE_END)
    job += command("DIRECTION", "1", line_end=_LINE_END)
    job += command("CLS", line_end=_LINE_END)
    job += bitmap_command(
        raster,
        line_end=_LINE_END,
        invert_bits=True,
    )
    job += command("PRINT", "1,1", line_end=_LINE_END)
    return bytes(job)


def _px_to_whole_mm(value: int, dpi: int) -> int:
    return int(float(value) * 25.4 / float(dpi))
