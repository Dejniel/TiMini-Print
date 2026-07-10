"""Phomemo ESC command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from .core import (
    advance_paper_cmd,
    build_phomemo_esc_job,
    retract_paper_cmd,
    supported_paper_modes,
)

def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.stream(build_phomemo_esc_job(request))


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.PHOMEMO_ESC_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.PHOMEMO_ESC_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=(
        "m02",
        "m02s",
        "m02x",
        "m02_pro",
        "t02",
        "m110",
        "m220",
        "printmaster_m110",
        "printmaster_m120",
    ),
    supported_paper_modes=(PaperMode.PLAIN,),
    supported_paper_modes_resolver=supported_paper_modes,
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
