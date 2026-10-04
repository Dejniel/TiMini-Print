"""Eleph ESC bitmap jobs; JX session queries live outside the print stream."""

from __future__ import annotations

from ...raster import PixelFormat
from ..plan import ProtocolPlan
from ..types import ImageEncoding, ImagePipelineConfig, PaperMode
from .base import PrintJobRequest, ProtocolBehavior
from .bitmap import build_gs_v0_blocks, build_zlib_raster_frame, packed_row_width_bytes


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    if request.protocol_variant not in (None, "p1"):
        raise ValueError(f"Unsupported Eleph ESC variant: {request.protocol_variant}")
    raster = request.require_raster(PixelFormat.BW1)
    raster.validate()
    stride = packed_row_width_bytes(raster.width)
    if stride > 0xFFFF or raster.height > 0xFFFF:
        raise ValueError("Eleph ESC bitmap dimensions exceed the 16-bit header")
    if request.image_pipeline.encoding is ImageEncoding.ELEPH_ESC_ZLIB:
        bitmap = build_zlib_raster_frame(
            raster, command=b"\x1b#!",
            window_bits=10, level=-1, memory_level=9,
        )
    else:
        # Both mode bytes are ASCII '0', not the binary mode used by other ESC dialects.
        bitmap = build_gs_v0_blocks(raster, max_lines_per_block=0xFFFF, mode=0x30)
    return ProtocolPlan.stream(b"\x1b@\x1ba\x01" + bitmap + b"\x1d\x0c")


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,), encoding=ImageEncoding.ELEPH_ESC_RAW,
    ),
    image_encoding_support={
        ImageEncoding.ELEPH_ESC_RAW: (PixelFormat.BW1,),
        ImageEncoding.ELEPH_ESC_ZLIB: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("p1",),
    supported_paper_modes=(PaperMode.TAG,),
    job_builder=build_job,
)
