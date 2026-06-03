from __future__ import annotations

from dataclasses import dataclass

from ...raster import PixelFormat, RasterBuffer
from ..encoding import pack_line
from ..types import PaperMode
from .base import PrintJobRequest


_LINE_END = b"\r\n"
_P1_ESC_PAPER_TYPE_COMMAND = bytes([0x10, 0xFF, 0x10, 0x03])
_P1_ESC_PAPER_TYPE_CONTINUOUS_REEL = 0x01
_P1_ESC_PAPER_TYPE_NO_DRY_ADHESIVE = 0x02


@dataclass(frozen=True)
class TsplMediaSetup:
    command: bytes

    def build(self, paper_type: int) -> bytes:
        return self.command + bytes([paper_type])


@dataclass(frozen=True)
class TsplPaperRecipe:
    media_paper_type: int
    gap_mm: float
    height_extra_mm: float = 0.0
    include_speed: bool = True


@dataclass(frozen=True)
class TsplRecipe:
    protocol_variant: str
    paper_recipes: dict[PaperMode, TsplPaperRecipe]
    default_paper_mode: PaperMode = PaperMode.TAG
    default_density: int = 8
    default_direction: int = 0
    default_mirror: int | None = None
    media_setup: TsplMediaSetup | None = None
    include_ribbon_off: bool = False
    include_reference_origin: bool = False

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(f"Unsupported TSPL protocol variant: {request.protocol_variant}")
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        width_bytes = _width_bytes(raster)
        density = _density(request.density, default=self.default_density)
        paper_recipe = self._paper_recipe(request.paper_mode)
        bitmap = _bitmap_payload(raster)

        job = bytearray()
        if self.media_setup is not None:
            job += self.media_setup.build(paper_recipe.media_paper_type)
        job += _command(
            "SIZE",
            (
                f"{_px_to_mm(raster.width, request.dev_dpi)} mm,"
                f"{_px_to_mm(raster.height, request.dev_dpi, extra_mm=paper_recipe.height_extra_mm)} mm"
            ),
        )
        job += _command("DIRECTION", _direction_value(self.default_direction, self.default_mirror))
        job += _command("GAP", f"{_format_mm(paper_recipe.gap_mm)} mm,0 mm")
        if self.include_ribbon_off:
            job += _command("SET RIBBON", "OFF")
        job += _command("DENSITY", str(density))
        if self.include_reference_origin:
            job += _command("REFERENCE", "0,0")
        if paper_recipe.include_speed and request.speed is not None:
            job += _command("SPEED", str(request.speed))
        job += _command("CLS")
        job += (
            _command_head("BITMAP", f"0,0,{width_bytes},{raster.height},0,")
            + bitmap
            + _LINE_END
        )
        job += _command("PRINT", "1,1")
        return bytes(job)

    def _paper_recipe(self, paper_mode: PaperMode | None) -> TsplPaperRecipe:
        resolved_mode = self.default_paper_mode if paper_mode is None else paper_mode
        return self.paper_recipes[resolved_mode]


def build_p1_job(request: PrintJobRequest) -> bytes:
    return TsplRecipe(
        protocol_variant="p1",
        paper_recipes={
            PaperMode.TAG: TsplPaperRecipe(
                media_paper_type=_P1_ESC_PAPER_TYPE_NO_DRY_ADHESIVE,
                gap_mm=3.0,
            ),
            PaperMode.PLAIN: TsplPaperRecipe(
                media_paper_type=_P1_ESC_PAPER_TYPE_CONTINUOUS_REEL,
                gap_mm=0.0,
                height_extra_mm=5.0,
                include_speed=False,
            ),
        },
        default_density=9,
        default_mirror=0,
        media_setup=TsplMediaSetup(command=_P1_ESC_PAPER_TYPE_COMMAND),
        include_ribbon_off=True,
        include_reference_origin=True,
    ).build_job(request)


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


def _direction_value(direction: int, mirror: int | None) -> str:
    if mirror is None:
        return str(direction)
    return f"{direction},{mirror}"


def _px_to_mm(value: int, dpi: int, *, extra_mm: float = 0.0) -> str:
    return _format_mm(float(value) * 25.4 / float(dpi) + extra_mm)


def _format_mm(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _command(name: str, value: str | None = None) -> bytes:
    return _command_head(name, value) + _LINE_END


def _command_head(name: str, value: str | None = None) -> bytes:
    if value is None:
        return name.encode("ascii")
    return f"{name} {value}".encode("ascii")
