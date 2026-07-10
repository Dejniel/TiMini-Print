"""Eleph/ToPrint P1 TSPL-shaped command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from .core import (
    advance_paper_cmd,
    build_p1_job,
    retract_paper_cmd,
)

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
    supported_paper_modes=(PaperMode.TAG, PaperMode.PLAIN),
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
