"""Funny Print LX BLE command dialect."""

from __future__ import annotations

from dataclasses import dataclass

from ....raster import PixelFormat, RasterBuffer
from ...steps import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from ..base import PrintJobRequest
from ..bitmap import pack_bw1_rows

_PRINTHEAD_WIDTH_PX = 384
_PACKET_DATA_BYTES = 96
_PACKET_HALF_BYTES = 48
_DEFAULT_DARKNESS_LEVEL = 4
_DIRECT_VARIANT = "lx_d_direct"
_REVERSED_VARIANT = "lx_d_reversed"
_SUPPORTED_VARIANTS = frozenset({_DIRECT_VARIANT, _REVERSED_VARIANT})


@dataclass(frozen=True)
class FunnyLxCrc:
    low: bytes
    high: bytes


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def challenge_crc(random_bytes: bytes, mac_bytes: bytes) -> FunnyLxCrc:
    if len(mac_bytes) != 6:
        raise ValueError("Funny LX MAC must contain 6 bytes")
    low = bytearray()
    high = bytearray()
    for random_byte in random_bytes:
        crc = crc16_xmodem(bytes([random_byte]) + mac_bytes)
        low.append(crc & 0xFF)
        high.append((crc >> 8) & 0xFF)
    return FunnyLxCrc(low=bytes(low), high=bytes(high))


def build_funny_lx_job(request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
    variant = request.protocol_variant or _DIRECT_VARIANT
    if variant not in _SUPPORTED_VARIANTS:
        raise ValueError(f"Unsupported Funny LX protocol variant: {request.protocol_variant!r}")
    if request.image_pipeline.encoding.value != "funny_lx_raster":
        raise ValueError(
            f"Unsupported Funny LX image encoding: {request.image_pipeline.encoding.value}"
        )
    raster = request.require_raster(PixelFormat.BW1)
    raster.validate()
    if raster.width != _PRINTHEAD_WIDTH_PX:
        raise ValueError(f"Funny LX jobs require {_PRINTHEAD_WIDTH_PX}px raster width")

    content = pack_bw1_rows(raster, lsb_first=False)
    total_packets = (len(content) + _PACKET_DATA_BYTES - 1) // _PACKET_DATA_BYTES
    total = _u16be(total_packets)
    steps: list[ProtocolStep] = []
    if request.is_first_page:
        steps.append(
            ProtocolStep.send(
                "darkness",
                bytes([0x5A, 0x0C, _darkness_level(request.blackening)]),
            )
        )
    steps.append(ProtocolStep.send("print header", b"\x5A\x04" + total + b"\x00\x00"))
    steps.extend(
        ProtocolStep.send(f"image packet {index}", packet)
        for index, packet in enumerate(_image_packets(content, variant=variant), start=1)
    )
    steps.append(
        ProtocolStep.wait(
            "image accepted",
            reply_matcher=_prefix_matcher(b"\x5A\x06"),
            timeout_sec=2.0,
        )
    )
    steps.append(
        ProtocolStep.query(
            "print footer",
            b"\x5A\x04" + total + b"\x01",
            expect=ProtocolReplyExpectation.NONE,
            timeout_sec=2.0,
            reply_matcher=_footer_matcher(total),
        )
    )
    # TODO: implement `5A 05 <packet-index>` retry/resume. The source app backs
    # up to the requested image packet and resumes after the printer asks for it.
    return tuple(steps)


def _image_packets(content: bytes, *, variant: str) -> tuple[bytes, ...]:
    command_code = bytes(reversed(content)) if variant == _REVERSED_VARIANT else content
    packets: list[bytes] = []
    for index, offset in enumerate(range(0, len(command_code), _PACKET_DATA_BYTES)):
        block = command_code[offset : offset + _PACKET_DATA_BYTES]
        if len(block) < _PACKET_DATA_BYTES:
            block += b"\x00" * (_PACKET_DATA_BYTES - len(block))
        if variant == _REVERSED_VARIANT:
            block = block[:_PACKET_HALF_BYTES][::-1] + block[_PACKET_HALF_BYTES:][::-1]
        packets.append(b"\x55" + _u16be(index) + block + b"\x00")
    return tuple(packets)


def _darkness_level(blackening: int) -> int:
    level = max(1, min(5, int(blackening or _DEFAULT_DARKNESS_LEVEL)))
    return level - 1


def _u16be(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ValueError(f"Funny LX value does not fit uint16: {value}")
    return value.to_bytes(2, "big")


def _prefix_matcher(prefix: bytes) -> ProtocolReplyMatcher:
    def complete(raw: bytes) -> bool:
        return raw.startswith(prefix)

    def matches(raw: bytes | None) -> bool:
        return bool(raw and complete(raw))

    return ProtocolReplyMatcher(complete=complete, matches=matches)


def _footer_matcher(total: bytes) -> ProtocolReplyMatcher:
    def complete(raw: bytes) -> bool:
        return len(raw) >= 5 and raw[:2] == b"\x5A\x04" and raw[2:4] == total and raw[4] == 1

    def matches(raw: bytes | None) -> bool:
        return bool(raw and complete(raw))

    return ProtocolReplyMatcher(complete=complete, matches=matches)
