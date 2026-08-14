"""ToPrint ``zl=0`` TSPL-like commands.

This is TSPL-shaped command text plus app-specific setup commands, not a
generic TSPL implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....raster import PixelFormat, RasterBuffer
from ...types import PaperMode
from .._tspl import bitmap_command, command
from ..base import PrintJobRequest


_LINE_END = b"\r\n"
_P1_ESC_PAPER_TYPE_COMMAND = bytes([0x10, 0xFF, 0x10, 0x03])
_P1_ESC_PAPER_TYPE_CONTINUOUS_REEL = 0x01
_P1_ESC_PAPER_TYPE_NO_DRY_ADHESIVE = 0x02
_P1_ESC_PAPER_TYPE_HOLE = 0x03


@dataclass(frozen=True)
class ToPrintTsplMediaSetup:
    command: bytes

    def build(self, paper_type: int) -> bytes:
        return self.command + bytes([paper_type])


@dataclass(frozen=True)
class ToPrintTsplPaperRecipe:
    media_paper_type: int
    gap_mm: float
    sensor_command: str = "GAP"
    height_extra_mm: float = 0.0
    include_speed: bool = True


@dataclass(frozen=True)
class ToPrintTsplRecipe:
    protocol_variant: str
    paper_recipes: dict[PaperMode, ToPrintTsplPaperRecipe]
    default_paper_mode: PaperMode = PaperMode.TAG
    default_density: int = 8
    default_direction: int = 0
    default_mirror: int | None = None
    media_setup: ToPrintTsplMediaSetup | None = None
    include_ribbon_off: bool = False
    include_reference_origin: bool = False

    def build_job(self, request: PrintJobRequest) -> bytes:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(f"Unsupported ToPrint TSPL protocol variant: {request.protocol_variant}")
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        density = _density(request.density, default=self.default_density)
        paper_recipe = self._paper_recipe(request.paper_mode)
        height_extra_mm = paper_recipe.height_extra_mm if request.ends_media_page else 0.0

        job = bytearray()
        if self.media_setup is not None:
            job += self.media_setup.build(paper_recipe.media_paper_type)
        job += command(
            "SIZE",
            (
                f"{_px_to_mm(raster.width, request.dev_dpi)} mm,"
                f"{_px_to_mm(raster.height, request.dev_dpi, extra_mm=height_extra_mm)} mm"
            ),
            line_end=_LINE_END,
        )
        job += command(
            "DIRECTION",
            _direction_value(self.default_direction, self.default_mirror),
            line_end=_LINE_END,
        )
        job += command(
            paper_recipe.sensor_command,
            f"{_format_mm(paper_recipe.gap_mm)} mm,0 mm",
            line_end=_LINE_END,
        )
        if self.include_ribbon_off:
            job += command("SET RIBBON", "OFF", line_end=_LINE_END)
        job += command("DENSITY", str(density), line_end=_LINE_END)
        if self.include_reference_origin:
            job += command("REFERENCE", "0,0", line_end=_LINE_END)
        if paper_recipe.include_speed and request.speed is not None:
            job += command("SPEED", str(request.speed), line_end=_LINE_END)
        job += command("CLS", line_end=_LINE_END)
        job += bitmap_command(raster, line_end=_LINE_END)
        job += command("PRINT", "1,1", line_end=_LINE_END)
        return bytes(job)

    def _paper_recipe(self, paper_mode: PaperMode | None) -> ToPrintTsplPaperRecipe:
        resolved_mode = self.default_paper_mode if paper_mode is None else paper_mode
        return self.paper_recipes[resolved_mode]


def build_p1_job(request: PrintJobRequest) -> bytes:
    return ToPrintTsplRecipe(
        protocol_variant="p1",
        paper_recipes={
            PaperMode.TAG: ToPrintTsplPaperRecipe(
                media_paper_type=_P1_ESC_PAPER_TYPE_NO_DRY_ADHESIVE,
                gap_mm=3.0,
            ),
            PaperMode.PLAIN: ToPrintTsplPaperRecipe(
                media_paper_type=_P1_ESC_PAPER_TYPE_CONTINUOUS_REEL,
                gap_mm=0.0,
                height_extra_mm=5.0,
                include_speed=False,
            ),
            PaperMode.BLACK_TAG: ToPrintTsplPaperRecipe(
                media_paper_type=_P1_ESC_PAPER_TYPE_HOLE,
                gap_mm=3.0,
                sensor_command="BLINE",
                include_speed=False,
            ),
        },
        default_density=9,
        default_mirror=0,
        media_setup=ToPrintTsplMediaSetup(command=_P1_ESC_PAPER_TYPE_COMMAND),
        include_ribbon_off=True,
        include_reference_origin=True,
    ).build_job(request)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return command("FORMFEED", line_end=_LINE_END)


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return command("BACKFEED", "40", line_end=_LINE_END)


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
