"""Funny Print LX BLE command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from .core import build_funny_lx_job


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(build_funny_lx_job(request))


BEHAVIOR = ProtocolBehavior(
    print_controls=("blackening",),
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.FUNNY_LX_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.FUNNY_LX_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("lx_d_direct",),
    supported_paper_modes=(PaperMode.PLAIN,),
    job_builder=build_job,
)
