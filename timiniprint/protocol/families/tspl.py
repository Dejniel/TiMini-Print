from __future__ import annotations

from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig, PaperMode
from .base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .tspl_core import (
    advance_paper_cmd,
    build_p1_job,
    retract_paper_cmd,
)

_P1_SERVICE_UUID = "000018f0-0000-1000-8000-00805f9b34fb"
_P1_WRITE_UUID = "00002af1-0000-1000-8000-00805f9b34fb"
_STANDARD_CHUNK_CAP = 180
_STANDARD_WRITE_DELAY_MS = 10


def build_job(request: PrintJobRequest) -> bytes:
    return build_p1_job(request)


TRANSPORT = BleTransportProfile(
    preferred_service_uuid=_P1_SERVICE_UUID,
    preferred_write_char_uuid=_P1_WRITE_UUID,
    standard_chunk_cap=_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_STANDARD_WRITE_DELAY_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.TSPL_BITMAP,
    ),
    image_encoding_support={
        ImageEncoding.TSPL_BITMAP: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("p1",),
    supported_paper_modes=(PaperMode.TAG, PaperMode.PLAIN),
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
