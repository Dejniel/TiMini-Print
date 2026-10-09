"""ToPrint ``zl=0`` TSPL-like command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import PrintJobRequest, ProtocolBehavior
from .core import build_p1_job


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.stream(build_p1_job(request))


BEHAVIOR = ProtocolBehavior(
    print_controls=("blackening",),
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.TOPRINT_TSPL_BITMAP,
    ),
    image_encoding_support={
        ImageEncoding.TOPRINT_TSPL_BITMAP: (PixelFormat.BW1,),
        ImageEncoding.TOPRINT_TSPL_ZLIB: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("p1",),
    supported_paper_modes=(PaperMode.TAG, PaperMode.PLAIN, PaperMode.BLACK_TAG),
    job_builder=build_job,
)
