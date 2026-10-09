"""ToPrint ``zl=1`` PSDK ESC raster and job envelope."""

from __future__ import annotations

from ....raster import PixelFormat
from ...steps import ProtocolStep
from ...types import ImageEncoding
from ..base import PrintJobRequest
from ..bitmap import build_gs_v0_single_raster, build_zlib_raster_frame
from ..toprint_control import paper_type_cmd


_MANUAL_PAPER_MOTION_DOTS = 80  # TODO: Calibrate this UI distance against real devices.


def build_zl1_job(request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
    if request.protocol_variant not in (None, "zl1"):
        raise ValueError(f"Unsupported ToPrint HPRT/ESC protocol variant: {request.protocol_variant}")
    raster = request.require_raster(PixelFormat.BW1)
    if request.image_pipeline.encoding is ImageEncoding.TOPRINT_HPRT_ESC_ZLIB:
        image = build_zlib_raster_frame(
            raster, command=b"\x1f\x00", window_bits=10, level=-1, omit_zlib_header=True,
        )
    else:
        image = build_gs_v0_single_raster(raster)
    thickness = max(0, min(255, request.density if request.density is not None else 1))
    body = (
        b"\x10\xff\xfe\x01" + bytes(12) + b"\x1b\x61\x01" + image
        + (b"\x1d\x0c" if request.ends_media_page else b"")
        + b"\x10\xff\xfe\x45\x10\xff\x10\x00" + bytes((thickness,))
    )
    return (
        ProtocolStep.send("hprt-media-type", paper_type_cmd(request.paper_mode)),
        ProtocolStep.send("hprt-esc-job", body),
    )


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b"\x1b\x4a" + bytes((_MANUAL_PAPER_MOTION_DOTS,))


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return b"\x10\xff\x81" + bytes((_MANUAL_PAPER_MOTION_DOTS,))
