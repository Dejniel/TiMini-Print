from __future__ import annotations

from dataclasses import dataclass

from ...raster import PixelFormat, RasterBuffer
from ..encoding import pack_line
from ..types import PaperMode
from .base import PrintJobRequest


_LINE_END = b"\r\n"


@dataclass(frozen=True)
class TsplRecipe:
    protocol_variant: str
    default_gap_mm: float = 2.0
    default_density: int = 8
    default_direction: int = 0

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(f"Unsupported TSPL protocol variant: {request.protocol_variant}")
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        width_bytes = _width_bytes(raster)
        density = _density(request.density, default=self.default_density)
        gap_mm = 0.0 if request.paper_mode == PaperMode.PLAIN else self.default_gap_mm
        bitmap = _bitmap_payload(raster)

        job = bytearray()
        job += _command("SIZE", f"{_px_to_mm(raster.width, request.dev_dpi)} mm,{_px_to_mm(raster.height, request.dev_dpi)} mm")
        job += _command("GAP", f"{_format_mm(gap_mm)} mm,0 mm")
        if request.speed is not None:
            job += _command("SPEED", str(request.speed))
        job += _command("DENSITY", str(density))
        job += _command("CLS")
        job += _command("DIRECTION", str(self.default_direction))
        job += (
            _command_head("BITMAP", f"0,0,{width_bytes},{raster.height},0")
            + bitmap
            + _LINE_END
        )
        job += _command("PRINT", "1,1")
        return bytes(job)


def build_p1_job(request: PrintJobRequest) -> bytes:
    return TsplRecipe(protocol_variant="p1").build_job(request)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _command("FORMFEED")


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _command("BACKFEED", "40")


def _bitmap_payload(raster: RasterBuffer) -> bytes:
    if raster.width % 8 != 0:
        raise ValueError("TSPL bitmap jobs require width divisible by 8")
    payload = bytearray()
    for row in range(raster.height):
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        payload += pack_line(list(line), lsb_first=False)
    return bytes(payload)


def _width_bytes(raster: RasterBuffer) -> int:
    if raster.width % 8 != 0:
        raise ValueError("TSPL bitmap jobs require width divisible by 8")
    return raster.width // 8


def _density(value: int | None, *, default: int) -> int:
    if value is None:
        value = default
    return max(0, min(15, int(value)))


def _px_to_mm(value: int, dpi: int) -> str:
    return _format_mm(float(value) * 25.4 / float(dpi))


def _format_mm(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _command(name: str, value: str | None = None) -> bytes:
    return _command_head(name, value) + _LINE_END


def _command_head(name: str, value: str | None = None) -> bytes:
    if value is None:
        return name.encode("ascii")
    return f"{name} {value}".encode("ascii")
