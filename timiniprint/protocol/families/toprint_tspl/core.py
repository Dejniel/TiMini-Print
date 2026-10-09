"""ToPrint ``zl=0`` TSPL-shaped label and continuous-paper recipe."""

from __future__ import annotations

from ....raster import PixelFormat
from ...types import ImageEncoding, PaperMode
from .._tspl import bitmap_command, command, zlib_bitmap_command
from ..base import PrintJobRequest
from ..toprint_control import paper_type_cmd


_LINE_END = b"\r\n"


def build_p1_job(request: PrintJobRequest) -> bytes:
    if request.protocol_variant not in (None, "p1"):
        raise ValueError(f"Unsupported ToPrint TSPL protocol variant: {request.protocol_variant}")
    raster = request.require_raster(PixelFormat.BW1)
    raster.validate()
    width, height = raster.width, raster.height
    mode = request.paper_mode or PaperMode.TAG
    height_extra = 5.0 if mode is PaperMode.PLAIN and request.ends_media_page else 0.0

    job = bytearray(paper_type_cmd(mode))
    job += command(
        "SIZE", f"{_mm(width, request.dev_dpi)} mm,{_mm(height, request.dev_dpi, height_extra)} mm",
        line_end=_LINE_END,
    )
    job += command("DIRECTION", "0,0", line_end=_LINE_END)
    sensor = "BLINE" if mode is PaperMode.BLACK_TAG else "GAP"
    gap = 0 if mode is PaperMode.PLAIN else 3
    job += command(sensor, f"{gap} mm,0 mm", line_end=_LINE_END)
    job += command("SET RIBBON", "OFF", line_end=_LINE_END)
    density = max(0, min(15, request.density if request.density is not None else 9))
    job += command("DENSITY", str(density), line_end=_LINE_END)
    job += command("REFERENCE", "0,0", line_end=_LINE_END)
    if mode is PaperMode.TAG and request.speed is not None:
        job += command("SPEED", str(request.speed), line_end=_LINE_END)
    job += command("CLS", line_end=_LINE_END)
    if request.image_pipeline.encoding is ImageEncoding.TOPRINT_TSPL_ZLIB:
        job += zlib_bitmap_command(raster, line_end=_LINE_END * 2, window_bits=10, level=-1)
    else:
        job += bitmap_command(raster, line_end=_LINE_END * 2, invert_bits=True)
    job += command("PRINT", "1,1", line_end=_LINE_END)
    return bytes(job)


def _mm(pixels: int, dpi: int, extra: float = 0.0) -> str:
    dots_per_mm = 12 if dpi == 300 else 8
    return f"{pixels / dots_per_mm + extra:.2f}".rstrip("0").rstrip(".") or "0"
