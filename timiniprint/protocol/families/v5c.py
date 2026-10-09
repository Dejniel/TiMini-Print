from __future__ import annotations

from dataclasses import dataclass

from ..encoding import pack_line
from ..packet import make_packet
from ..plan import ProtocolPlan
from ..steps import ProtocolStep
from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig
from ..runtime import RuntimePrintCapabilities
from .base import PrintJobRequest, ProtocolBehavior
from .v5_common import build_lzo_band_frames


def _hex_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


V5C_CONNECT_INIT_PACKET = _hex_bytes("5688AA0001000000FF")
V5C_QUERY_STATUS_PACKET = _hex_bytes("5688A10001000000FF")
V5C_PRINT_START_PACKET = _hex_bytes("5688A30001000101FF")
V5C_NOTIFY_PAUSE = _hex_bytes("5688A70101000107FF")
V5C_NOTIFY_RESUME = _hex_bytes("5688A70101000000FF")
# Compressed V5C jobs are emitted in 20-row bands.
_V5C_BAND_ROWS = 20


@dataclass(frozen=True)
class V5CPrintCapabilities(RuntimePrintCapabilities):
    max_gray_height: int = 800

    def height_limit(self, encoding: ImageEncoding) -> int:
        return self.max_gray_height * (1 if encoding is ImageEncoding.V5C_A5 else 3)


def _settings_payload(blackening: int, encoding: ImageEncoding) -> bytes:
    level = max(1, min(5, blackening))
    # The protocol exposes only three density states even though the user-level
    # blackening setting has five steps.
    if level <= 2:
        density = 0x01
    elif level >= 4:
        density = 0x03
    else:
        density = 0x02
    mode = 0x02 if encoding is ImageEncoding.V5C_A5 else 0x01
    return bytes([density, mode])


def _build_a4_frames(request: PrintJobRequest) -> bytes:
    raster = request.require_raster(PixelFormat.BW1)
    job = bytearray()
    height = raster.height
    for row in range(height):
        line = raster.pixels[row * raster.width : (row + 1) * raster.width]
        job += make_packet(0xA4, pack_line(line, lsb_first=True), request.protocol_family)
    return bytes(job)


def _build_a5_frames(request: PrintJobRequest) -> bytes:
    gray_raster = request.default_raster
    if gray_raster.pixel_format not in (PixelFormat.GRAY4, PixelFormat.GRAY8):
        raise ValueError("V5C compressed jobs require GRAY4 or GRAY8 source raster")

    return build_lzo_band_frames(
        gray_raster,
        opcode=0xA5,
        rows_per_band=_V5C_BAND_ROWS,
        protocol_family=request.protocol_family,
    )


def _build_payload(request: PrintJobRequest) -> bytes:
    if request.width % 8 != 0:
        raise ValueError("Width must be divisible by 8")
    capabilities = request.runtime_capabilities
    if not isinstance(capabilities, V5CPrintCapabilities):
        capabilities = V5CPrintCapabilities()
    maximum = capabilities.height_limit(request.image_pipeline.encoding)
    if request.height > maximum:
        raise ValueError(f"V5C raster height {request.height} exceeds the {maximum}-row limit")

    job = bytearray()
    job += make_packet(
        0xA2,
        _settings_payload(request.blackening, request.image_pipeline.encoding),
        request.protocol_family,
    )
    job += V5C_PRINT_START_PACKET
    if request.image_pipeline.encoding == ImageEncoding.V5C_A5:
        job += _build_a5_frames(request)
    else:
        job += _build_a4_frames(request)

    job += make_packet(0xA6, bytes([0x30, 0x00]), request.protocol_family)
    return bytes(job)


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.sequence(
        (
            ProtocolStep.send("print data", _build_payload(request)),
            ProtocolStep.send("query status", V5C_QUERY_STATUS_PACKET),
        )
    )


BEHAVIOR = ProtocolBehavior(
    print_controls=("blackening",),
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1, PixelFormat.GRAY8, PixelFormat.GRAY4),
        encoding=ImageEncoding.V5C_A4,
    ),
    image_encoding_support={
        ImageEncoding.V5C_A4: (PixelFormat.BW1,),
        ImageEncoding.V5C_A5: (PixelFormat.GRAY8, PixelFormat.GRAY4),
    },
    job_builder=build_job,
)
