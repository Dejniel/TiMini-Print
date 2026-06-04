from __future__ import annotations

from ..compression import compress_lzo1x_1
from ..encoding import pack_line
from ..family import ProtocolFamily
from ..packet import make_packet
from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig
from .base import BleTransportProfile, PrintJobRequest, ProtocolBehavior

# Firmware blackening 1-5 maps to the protocol A4 quality bytes, not to
# literal energy values. Keep this as a lookup table rather than arithmetic.
_QUALITY_BY_LEVEL = (0x31, 0x32, 0x33, 0x34, 0x35)
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
_BLE_STANDARD_CHUNK_CAP = 56 * 8
_BLE_STANDARD_WRITE_DELAY_MS = 30
_BLE_WRITE_WITHOUT_RESPONSE_PAYLOAD_RESERVE = 5
V5G_CONNECT_QUERY_PACKET = bytes.fromhex("5178A30001000000FF")
V5G_TEMPERATURE_QUERY_PACKET = bytes.fromhex("5178D30001000000FF")


def encode_density_payload(density: int) -> bytes:
    value = max(_DENSITY_MIN, min(_DENSITY_MAX, int(density)))
    return bytes([_DENSITY_PAYLOAD_PREFIX, value])


def decode_density_payload(payload: bytes) -> int | None:
    if len(payload) != 2 or payload[0] != _DENSITY_PAYLOAD_PREFIX:
        return None
    return payload[1]


def _quality_packet(blackening: int, protocol_family) -> bytes:
    level = max(1, min(5, blackening))
    return make_packet(0xA4, bytes([_QUALITY_BY_LEVEL[level - 1]]), protocol_family)


def _energy_packet(energy: int, protocol_family) -> bytes:
    if energy <= 0:
        return b""
    return make_packet(0xAF, int(energy).to_bytes(2, "little", signed=False), protocol_family)


def _print_mode_packet(protocol_family) -> bytes:
    return make_packet(0xBE, bytes([0x00]), protocol_family)


def _feed_packet(speed: int, protocol_family) -> bytes:
    return make_packet(0xBD, bytes([speed & 0xFF]), protocol_family)


def _paper_packet(dev_dpi: int, protocol_family) -> bytes:
    # The paper motion distance differs between 203 dpi and 300 dpi heads.
    payload = bytes([0x48, 0x00]) if int(dev_dpi) == 300 else bytes([0x30, 0x00])
    return make_packet(0xA1, payload, protocol_family)


def _state_query_packet(protocol_family) -> bytes:
    family = ProtocolFamily.from_value(protocol_family)
    return make_packet(0xA3, bytes([0x00]), family)


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


def _gray_band_payload(raw_block: bytes) -> bytes:
    compressed = compress_lzo1x_1(raw_block)
    return (
        len(raw_block).to_bytes(2, "little")
        + len(compressed).to_bytes(2, "little")
        + compressed
    )


def _gray_frames(request: PrintJobRequest) -> bytes:
    raster = request.default_raster
    if raster.pixel_format not in (PixelFormat.GRAY4, PixelFormat.GRAY8):
        raise ValueError("V5G gray jobs require GRAY4 or GRAY8 raster data")

    job = bytearray()
    for row in range(0, raster.height, _GRAY_BAND_ROWS):
        rows = min(_GRAY_BAND_ROWS, raster.height - row)
        block = raster.slice_rows(row, rows).packed_bytes()
        job += make_packet(0xCF, _gray_band_payload(block), request.protocol_family)
    return bytes(job)


def build_job(request: PrintJobRequest) -> bytes:
    job = bytearray()
    if request.density is not None:
        job += _density_packet(request.density, request.protocol_family)
    job += _state_query_packet(request.protocol_family)
    job += _quality_packet(request.blackening, request.protocol_family)
    job += _lattice_packet(True, request.protocol_family)
    job += _energy_packet(request.energy, request.protocol_family)
    job += _print_mode_packet(request.protocol_family)
    job += _feed_packet(_PRE_IMAGE_FEED_SPEED, request.protocol_family)

    if request.image_pipeline.encoding == ImageEncoding.V5G_GRAY:
        job += _gray_frames(request)
    else:
        job += _dot_frames(request)
    job += _feed_packet(_POST_IMAGE_FEED_SPEED, request.protocol_family)

    for _ in range(max(0, request.post_print_feed_count)):
        job += _paper_packet(request.dev_dpi, request.protocol_family)
    job += _lattice_packet(False, request.protocol_family)
    job += _state_query_packet(request.protocol_family)
    job += _state_query_packet(request.protocol_family)
    return bytes(job)


TRANSPORT = BleTransportProfile(
    connect_packets=(V5G_CONNECT_QUERY_PACKET,),
    prefer_generic_notify=True,
    standard_chunk_cap=_BLE_STANDARD_CHUNK_CAP,
    standard_write_delay_ms=_BLE_STANDARD_WRITE_DELAY_MS,
    write_without_response_payload_reserve=_BLE_WRITE_WITHOUT_RESPONSE_PAYLOAD_RESERVE,
)


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
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
