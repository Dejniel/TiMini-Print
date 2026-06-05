from __future__ import annotations

from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig, PaperMode
from .base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .phomemo_escpos_core import (
    advance_paper_cmd,
    build_phomemo_escpos_job,
    retract_paper_cmd,
)

_PHOMEMO_SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
_PHOMEMO_WRITE_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
_STANDARD_CHUNK_CAP = 128
_STANDARD_WRITE_DELAY_MS = 20


def build_job(request: PrintJobRequest) -> bytes:
    return build_phomemo_escpos_job(request)


TRANSPORT = BleTransportProfile(
    preferred_service_uuid=_PHOMEMO_SERVICE_UUID,
    preferred_write_char_uuid=_PHOMEMO_WRITE_UUID,
    standard_chunk_cap=_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_STANDARD_WRITE_DELAY_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.PHOMEMO_ESCPOS_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.PHOMEMO_ESCPOS_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("m02", "m02s", "m02x", "m02_pro", "t02"),
    supported_paper_modes=(PaperMode.PLAIN,),
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
