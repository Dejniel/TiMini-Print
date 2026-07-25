from __future__ import annotations

from .._prefixed_commands import (
    blackening_cmd,
    dev_state_cmd,
    energy_cmd,
    feed_paper_cmd,
    paper_cmd,
    print_mode_cmd,
)
from ..encoding import pack_line
from ..packet import make_packet
from ..plan import ProtocolPlan
from ..steps import ProtocolStep
from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig
from .base import PrintJobRequest, ProtocolBehavior
from .v5_common import build_lzo_band_frames

# These A6 payloads are the fixed "lattice" envelopes used before and after
# a V5G print job.
_START_LATTICE = bytes.fromhex("AA551738445F5F5F44382C")
_FINISH_LATTICE = bytes.fromhex("AA55170000000000000017")
# Source V5G wrappers use fixed BD feed/control values around the image data.
# These are protocol command values, not the native BLE writer speed/delay.
_PRE_IMAGE_FEED_SPEED = 0x0A
_POST_IMAGE_FEED_SPEED = 0x19
_DENSITY_PAYLOAD_PREFIX = 0x01
_DENSITY_MIN = 1
_DENSITY_MAX = 200
# Gray jobs are sent in 20-row compressed bands.
_GRAY_BAND_ROWS = 20
# 384-dot V5G row packets are 56 bytes. Eight rows per BLE write keeps the
# transport out of the old 20-byte fallback path without changing payloads.
V5G_CONNECT_QUERY_PACKET = bytes.fromhex("5178A30001000000FF")
V5G_TEMPERATURE_QUERY_PACKET = bytes.fromhex("5178D30001000000FF")


def encode_density_payload(density: int) -> bytes:
    value = max(_DENSITY_MIN, min(_DENSITY_MAX, int(density)))
    return bytes([_DENSITY_PAYLOAD_PREFIX, value])


def decode_density_payload(payload: bytes) -> int | None:
    if len(payload) != 2 or payload[0] != _DENSITY_PAYLOAD_PREFIX:
        return None
    return payload[1]


def _lattice_packet(start: bool, protocol_family) -> bytes:
    payload = _START_LATTICE if start else _FINISH_LATTICE
    return make_packet(0xA6, payload, protocol_family)


def _density_packet(density: int, protocol_family) -> bytes:
    return make_packet(0xF2, encode_density_payload(density), protocol_family)


def _dot_frames(request: PrintJobRequest) -> bytes:
    raster = request.require_raster(PixelFormat.BW1)
    if raster.width % 8 != 0:
        raise ValueError("V5G dot jobs require width divisible by 8")
    job = bytearray()
    for row in range(raster.height):
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        job += make_packet(0xA2, pack_line(list(line), lsb_first=True), request.protocol_family)
    return bytes(job)


def _gray_frames(request: PrintJobRequest) -> bytes:
    raster = request.default_raster
    if raster.pixel_format not in (PixelFormat.GRAY4, PixelFormat.GRAY8):
        raise ValueError("V5G gray jobs require GRAY4 or GRAY8 raster data")

    return build_lzo_band_frames(
        raster,
        opcode=0xCF,
        rows_per_band=_GRAY_BAND_ROWS,
        protocol_family=request.protocol_family,
    )


def _build_payload(request: PrintJobRequest) -> bytes:
    job = bytearray()
    if request.density is not None:
        job += _density_packet(request.density, request.protocol_family)
    job += dev_state_cmd(request.protocol_family)
    job += blackening_cmd(request.blackening, request.protocol_family)
    job += _lattice_packet(True, request.protocol_family)
    job += energy_cmd(request.energy, request.protocol_family)
    job += print_mode_cmd(False, request.protocol_family)
    if request.starts_media_page:
        job += feed_paper_cmd(_PRE_IMAGE_FEED_SPEED, request.protocol_family)

    if request.image_pipeline.encoding == ImageEncoding.V5G_GRAY:
        job += _gray_frames(request)
    else:
        job += _dot_frames(request)
    if request.ends_media_page:
        job += feed_paper_cmd(_POST_IMAGE_FEED_SPEED, request.protocol_family)
        for _ in range(max(0, request.post_print_feed_count)):
            job += paper_cmd(request.dev_dpi, request.protocol_family)
    job += _lattice_packet(False, request.protocol_family)
    job += dev_state_cmd(request.protocol_family)
    job += dev_state_cmd(request.protocol_family)
    return bytes(job)


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(
        (ProtocolStep.send("print data", _build_payload(request)),)
    )


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1, PixelFormat.GRAY4, PixelFormat.GRAY8),
        encoding=ImageEncoding.V5G_DOT,
    ),
    image_encoding_support={
        ImageEncoding.V5G_DOT: (PixelFormat.BW1,),
        ImageEncoding.V5G_GRAY: (PixelFormat.GRAY4, PixelFormat.GRAY8),
    },
    job_builder=build_job,
)
