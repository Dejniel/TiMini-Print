"""Eleph/ToPrint HPRT ESC command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .core import (
    advance_paper_cmd,
    build_zl1_job,
    retract_paper_cmd,
)

_STANDARD_CHUNK_CAP = 180
_STANDARD_WRITE_DELAY_MS = 10


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(build_zl1_job(request))


TRANSPORT = BleTransportProfile(
    standard_chunk_cap=_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_STANDARD_WRITE_DELAY_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.ELEPH_HPRT_ESC_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.ELEPH_HPRT_ESC_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("zl1",),
    supported_paper_modes=(PaperMode.TAG, PaperMode.PLAIN, PaperMode.BLACK_TAG),
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
