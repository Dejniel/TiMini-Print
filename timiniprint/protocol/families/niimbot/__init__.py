from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig
from ..base import PrintJobRequest, ProtocolBehavior
from .core import build_niimbot_job

def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(build_niimbot_job(request))


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.NIIMBOT_D110,
    ),
    image_encoding_support={
        ImageEncoding.NIIMBOT_D110: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("d11_v1", "d110"),
    job_builder=build_job,
)
