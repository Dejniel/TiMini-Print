from __future__ import annotations

from ...raster import PixelFormat
from ..steps import ProtocolStep
from ..types import ImageEncoding, ImagePipelineConfig
from .base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .niimbot_core import build_d110_job

_NIIMBOT_SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
_PACKET_INTERVAL_MS = 10
_STANDARD_CHUNK_CAP = 20


def build_job(request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
    return build_d110_job(request)


TRANSPORT = BleTransportProfile(
    preferred_service_uuid=_NIIMBOT_SERVICE_UUID,
    prefer_generic_notify=True,
    standard_chunk_cap=_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_PACKET_INTERVAL_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.NIIMBOT_D110,
    ),
    image_encoding_support={
        ImageEncoding.NIIMBOT_D110: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("d110",),
    job_builder=build_job,
)
