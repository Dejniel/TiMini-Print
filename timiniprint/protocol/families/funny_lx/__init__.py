"""Funny Print LX BLE command dialect."""

from __future__ import annotations

from ....raster import PixelFormat
from ...plan import ProtocolPlan
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import BleTransportProfile, PrintJobRequest, ProtocolBehavior
from .core import build_funny_lx_job

_SERVICE_UUID = "0000ffe6-0000-1000-8000-00805f9b34fb"
_WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
_NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"
_PACKET_WRITE_BYTES = 100
_WRITE_DELAY_MS = 0
_FEED_PAPER_CMD = bytes.fromhex("5a 03 81 00 04 00 00 00 00 00 00 00")


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(build_funny_lx_job(request))


def advance_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    return _FEED_PAPER_CMD


def retract_paper_cmd(_dpi: int, _protocol_family, _protocol_variant: str | None = None) -> bytes:
    # TODO: no source-backed Funny LX retract command is known. The original app
    # exposes a fixed sendMovePaper() command, but does not model reverse motion.
    return b""


TRANSPORT = BleTransportProfile(
    preferred_service_uuid=_SERVICE_UUID,
    preferred_write_char_uuid=_WRITE_UUID,
    notify_char_uuid=_NOTIFY_UUID,
    standard_chunk_cap=_PACKET_WRITE_BYTES,
    standard_write_delay_ms=_WRITE_DELAY_MS,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.FUNNY_LX_RASTER,
    ),
    image_encoding_support={
        ImageEncoding.FUNNY_LX_RASTER: (PixelFormat.BW1,),
    },
    supported_protocol_variants=("lx_d_direct", "lx_d_reversed"),
    supported_paper_modes=(PaperMode.PLAIN,),
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
