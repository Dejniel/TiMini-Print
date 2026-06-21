"""InstaPrint/CorePrint small-printer command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .core import (
    advance_paper_cmd,
    build_instaprint_core_job,
    retract_paper_cmd,
    supported_paper_modes,
)

_STANDARD_CHUNK_CAP = 180
_STANDARD_WRITE_DELAY_MS = 10


def build_job(request: PrintJobRequest) -> bytes:
    return build_instaprint_core_job(request)


TRANSPORT = BleTransportProfile(
    standard_chunk_cap=_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_STANDARD_WRITE_DELAY_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.INSTAPRINT_CORE_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.INSTAPRINT_CORE_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("ctp500",),
    supported_paper_modes=(PaperMode.PLAIN,),
    supported_paper_modes_resolver=supported_paper_modes,
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
