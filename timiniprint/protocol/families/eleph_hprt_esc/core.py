"""Eleph/ToPrint HPRT ESC commands.

This is a source-app dialect built from ESC-shaped printer commands, not a
generic ESC/POS implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....raster import PixelFormat, RasterBuffer
from ...steps import ProtocolStep
from ...types import PaperMode
from ..base import PrintJobRequest
from ..bitmap import pack_bw1_rows, packed_row_width_bytes


_MEDIA_TYPE_CMD = bytes([0x10, 0xFF, 0x10, 0x03])
_ENABLE_CMD = bytes([0x10, 0xFF, 0xFE, 0x01])
_WAKEUP_CMD = bytes(12)
_LOCATION_CENTER_CMD = bytes([0x1B, 0x61, 0x01])
_IMAGE_CMD = bytes([0x1D, 0x76, 0x30])
_IMAGE_MODE_NORMAL = 0x00
_POSITION_CMD = bytes([0x1D, 0x0C])
_STOP_JOB_CMD = bytes([0x10, 0xFF, 0xFE, 0x45])
_THICKNESS_CMD = bytes([0x10, 0xFF, 0x10, 0x00])
_FEED_DOTS_CMD = bytes([0x1B, 0x4A])
_RETRACT_DOTS_CMD = bytes([0x10, 0xFF, 0x81])
_MANUAL_PAPER_MOTION_DOTS = 80  # TODO: Calibrate this UI motion distance against real devices.

_MEDIA_CONTINUOUS_REEL = 0x01
_MEDIA_NO_DRY_ADHESIVE = 0x02
_MEDIA_HOLE_PAPER = 0x03


@dataclass(frozen=True)
class ElephHprtEscPaperRecipe:
    media_paper_type: int


@dataclass(frozen=True)
class ElephHprtEscRecipe:
    protocol_variant: str
    paper_recipes: dict[PaperMode, ElephHprtEscPaperRecipe]
    default_paper_mode: PaperMode = PaperMode.TAG
    default_thickness: int = 1

    def build_job(self, request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
        if request.protocol_variant not in (None, self.protocol_variant):
            raise ValueError(f"Unsupported Eleph HPRT ESC protocol variant: {request.protocol_variant}")
        raster = request.require_raster(PixelFormat.BW1)
        recipe = self._paper_recipe(request.paper_mode)
        thickness = _thickness(request.density, default=self.default_thickness)

        return (
            ProtocolStep.send(
                "hprt-media-type",
                _MEDIA_TYPE_CMD + bytes([recipe.media_paper_type]),
            ),
            ProtocolStep.send(
                "hprt-esc-job",
                _esc_job(raster, thickness=thickness),
            ),
        )

    def _paper_recipe(self, paper_mode: PaperMode | None) -> ElephHprtEscPaperRecipe:
        resolved_mode = self.default_paper_mode if paper_mode is None else paper_mode
        return self.paper_recipes[resolved_mode]


def build_zl1_job(request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
    return ElephHprtEscRecipe(
        protocol_variant="zl1",
        paper_recipes={
            PaperMode.TAG: ElephHprtEscPaperRecipe(_MEDIA_NO_DRY_ADHESIVE),
            PaperMode.PLAIN: ElephHprtEscPaperRecipe(_MEDIA_CONTINUOUS_REEL),
            PaperMode.BLACK_TAG: ElephHprtEscPaperRecipe(_MEDIA_HOLE_PAPER),
        },
    ).build_job(request)


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_DOTS_CMD + bytes([_MANUAL_PAPER_MOTION_DOTS])


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _RETRACT_DOTS_CMD + bytes([_MANUAL_PAPER_MOTION_DOTS])


def _esc_job(raster: RasterBuffer, *, thickness: int) -> bytes:
    return (
        _ENABLE_CMD
        + _WAKEUP_CMD
        + _LOCATION_CENTER_CMD
        + _image_cmd(raster)
        + _POSITION_CMD
        + _STOP_JOB_CMD
        + _THICKNESS_CMD
        + bytes([thickness])
    )


def _image_cmd(raster: RasterBuffer) -> bytes:
    raster.validate()
    width_bytes = packed_row_width_bytes(raster.width)
    height = raster.height
    return (
        _IMAGE_CMD
        + bytes(
            [
                _IMAGE_MODE_NORMAL,
                width_bytes & 0xFF,
                (width_bytes >> 8) & 0xFF,
                height & 0xFF,
                (height >> 8) & 0xFF,
            ]
        )
        + pack_bw1_rows(raster, lsb_first=False)
    )


def _thickness(value: int | None, *, default: int) -> int:
    if value is None:
        value = default
    return max(0, min(255, int(value)))
