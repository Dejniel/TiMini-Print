from __future__ import annotations

from ...raster import PixelFormat
from ..plan import ProtocolPlan
from ..types import ImageEncoding, ImagePipelineConfig
from .base import PrintJobRequest, ProtocolBehavior


def build_job(_request: PrintJobRequest) -> ProtocolPlan:
    raise NotImplementedError("Printing is not implemented for the DCK protocol family yet")


BEHAVIOR = ProtocolBehavior(
    implemented=False,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.DCK_DEFAULT,
    ),
    image_encoding_support={
        ImageEncoding.DCK_DEFAULT: (PixelFormat.BW1,),
    },
    job_builder=build_job,
)
