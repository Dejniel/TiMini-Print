from __future__ import annotations

from ..encoding import pack_line
from ..packet import crc8_value, make_packet
from ..family import ProtocolFamily
from ..plan import ProtocolPlan
from ...raster import PixelFormat
from ..types import ImageEncoding, ImagePipelineConfig
from .base import BleBulkWriteProfile, BleTransportProfile, FlowControlProfile, PrintJobRequest, ProtocolBehavior

def _hex_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


V5X_SERVICE_UUID = "0000ae30-0000-1000-8000-00805f9b34fb"
V5X_BULK_DATA_UUID = "0000ae03-0000-1000-8000-00805f9b34fb"
V5X_NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

# Fixed packets and notify markers used by the V5X BLE workflow.
V5X_GET_SERIAL_PACKET = _hex_bytes("2221A70000000000")
V5X_CONNECT_INIT_PACKET = _hex_bytes("2221B10001000000FF")
V5X_STATUS_POLL_PACKET = _hex_bytes("2221A300020000000000")
V5X_FINALIZE_PACKET = _hex_bytes("2221AD000100000000")
V5X_NOTIFY_GET_SERIAL_ACK = _hex_bytes("2221A7000000")
V5X_NOTIFY_START_READY = _hex_bytes("2221AA0000")
V5X_NOTIFY_START_PRINT_OK = _hex_bytes("2221A9000000")
V5X_NOTIFY_TRIGGER_STATUS_POLL = _hex_bytes("2221B20000")
V5X_NOTIFY_IDLE_GET_SERIAL = _hex_bytes("2221A60000")

_MANUAL_MOTION_PAYLOAD = bytes([0x05, 0x00])
V5X_LABEL_MODE_SUFFIX = _hex_bytes("30010000")
V5X_GRAY_MODE_SUFFIX = _hex_bytes("30020000")
V5X_STANDARD_MODE_SUFFIX = _hex_bytes("30000000")
# Firmware blackening 1-5 maps to different density bytes for dot and gray
# jobs; the values are not linear.
_DOT_DENSITY_BY_LEVEL = (0x58, 0x5A, 0x5D, 0x5F, 0x62)
_GRAY_DENSITY_BY_LEVEL = (0x4B, 0x50, 0x55, 0x5A, 0x62)
# Pause/resume packets were captured as a finite set of flow-control markers
# across the known V5X-compatible firmwares.
_FLOW_PAUSE_HEX = (
    "AA01",
    "5178AE0101001070FF",
    "2221A800010020E0FF",
    "2221AE0101001070FF",
    "2221AE0001000000",
)
_FLOW_RESUME_HEX = (
    "AA00",
    "5178AE0101000000FF",
    "2221A80001003090FF",
    "2221AE0101000000FF",
    "2221AE0001001000",
)
_BLE_BULK_CHUNK_CAP = 180
_BLE_BULK_WRITE_DELAY_MS = 30
_BLE_WRITE_WITHOUT_RESPONSE_PAYLOAD_RESERVE = 5

_FLOW_CONTROL = FlowControlProfile(
    pause_packets=frozenset(_hex_bytes(value) for value in _FLOW_PAUSE_HEX),
    resume_packets=frozenset(_hex_bytes(value) for value in _FLOW_RESUME_HEX),
)


TRANSPORT = BleTransportProfile(
    connect_packets=(V5X_CONNECT_INIT_PACKET,),
    connect_delay_ms=200,
    preferred_service_uuid=V5X_SERVICE_UUID,
    notify_char_uuid=V5X_NOTIFY_UUID,
    flow_control=_FLOW_CONTROL,
    # V5X bulk writes stay stable at 180-byte chunks even when the negotiated
    # MTU is larger.
    bulk_write=BleBulkWriteProfile(
        char_uuid=V5X_BULK_DATA_UUID,
        chunk_cap=_BLE_BULK_CHUNK_CAP,
        write_delay_ms=_BLE_BULK_WRITE_DELAY_MS,
        tail_packets=(V5X_FINALIZE_PACKET,),
    ),
    write_without_response_payload_reserve=_BLE_WRITE_WITHOUT_RESPONSE_PAYLOAD_RESERVE,
)


def _raw_lsb_payload(pixels: list[int] | tuple[int, ...], width: int) -> bytes:
    if width % 8 != 0:
        raise ValueError("Width must be divisible by 8")
    if len(pixels) % width != 0:
        raise ValueError("Pixel count must be a multiple of width")
    payload = bytearray()
    height = len(pixels) // width
    for row in range(height):
        line = pixels[row * width : (row + 1) * width]
        payload += pack_line(line, lsb_first=True)
    return bytes(payload)


def _gray_payload(raster) -> bytes:
    if raster.pixel_format == PixelFormat.GRAY8:
        return bytes(raster.pixels)
    if raster.pixel_format == PixelFormat.GRAY4:
        return raster.packed_bytes()
    raise ValueError("V5X gray jobs require GRAY4 or GRAY8 raster data")


def advance_paper_cmd(
    _dpi: int,
    protocol_family: ProtocolFamily,
    _protocol_variant: str | None = None,
) -> bytes:
    return make_packet(0xA3, _MANUAL_MOTION_PAYLOAD, protocol_family)


def retract_paper_cmd(
    _dpi: int,
    protocol_family: ProtocolFamily,
    _protocol_variant: str | None = None,
) -> bytes:
    return make_packet(0xA4, _MANUAL_MOTION_PAYLOAD, protocol_family)


def _density_payload(request: PrintJobRequest) -> bytes:
    level = max(1, min(5, request.blackening))
    table = (
        _GRAY_DENSITY_BY_LEVEL
        if request.image_pipeline.encoding == ImageEncoding.V5X_GRAY
        else _DOT_DENSITY_BY_LEVEL
    )
    return bytes([table[level - 1]])


def _start_print_mode_suffix(request: PrintJobRequest) -> bytes:
    if request.image_pipeline.encoding == ImageEncoding.V5X_GRAY:
        return V5X_GRAY_MODE_SUFFIX
    if request.can_print_label:
        return V5X_LABEL_MODE_SUFFIX
    return V5X_STANDARD_MODE_SUFFIX


def _start_print_payload(height: int, request: PrintJobRequest) -> bytes:
    return height.to_bytes(2, "little") + _start_print_mode_suffix(request)


def _gray_start_packet(height: int, protocol_family: ProtocolFamily) -> bytes:
    family = ProtocolFamily.from_value(protocol_family)
    height_bytes = height.to_bytes(2, "little")
    header = family.require_packet_prefix() + bytes([0xA9, 0x00, 0x02, 0x00])
    return header + height_bytes + bytes([crc8_value(height_bytes), 0xFF])


def _build_payload(request: PrintJobRequest) -> bytes:
    is_gray = request.image_pipeline.encoding == ImageEncoding.V5X_GRAY
    raster = (
        request.default_raster
        if is_gray
        else request.require_raster(PixelFormat.BW1)
    )
    height = raster.height
    job = bytearray()
    job += V5X_GET_SERIAL_PACKET
    job += make_packet(0xA2, _density_payload(request), request.protocol_family)
    if is_gray:
        job += _gray_start_packet(height, request.protocol_family)
        job += _gray_payload(raster)
    else:
        job += make_packet(0xA9, _start_print_payload(height, request), request.protocol_family)
        job += _raw_lsb_payload(list(raster.pixels), raster.width)
    job += V5X_FINALIZE_PACKET
    return bytes(job)


def build_job(request: PrintJobRequest) -> ProtocolPlan:
    return ProtocolPlan.stream(_build_payload(request))


BEHAVIOR = ProtocolBehavior(
    transport=TRANSPORT,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1, PixelFormat.GRAY4, PixelFormat.GRAY8),
        encoding=ImageEncoding.V5X_DOT,
    ),
    image_encoding_support={
        ImageEncoding.V5X_DOT: (PixelFormat.BW1,),
        ImageEncoding.V5X_GRAY: (PixelFormat.GRAY4, PixelFormat.GRAY8),
    },
    advance_paper_builder=advance_paper_cmd,
    retract_paper_builder=retract_paper_cmd,
    job_builder=build_job,
)
