"""Eleph-label P1 TSPL command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from .core import build_p1_job

def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.stream(build_p1_job(request))


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.ELEPH_TSPL_BITMAP,
    ),
    image_encoding_support={
        ImageEncoding.ELEPH_TSPL_BITMAP: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("p1",),
    supported_paper_modes=(PaperMode.TAG,),
    job_builder=build_job,
)
