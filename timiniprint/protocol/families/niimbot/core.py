from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Iterable

from ....raster import PixelFormat, RasterBuffer
from ...encoding import pack_line
from ...steps import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from ..base import PrintJobRequest

_BIT_COUNTS = tuple(bin(value).count("1") for value in range(256))


class NiimbotRequest(IntEnum):
    CONNECT = 0xC1
    PRINT_START = 0x01
    PAGE_START = 0x03
    SET_PAGE_SIZE = 0x13
    PRINT_QUANTITY = 0x15
    PRINT_BITMAP_ROW_INDEXED = 0x83
    PRINT_BITMAP_ROW = 0x85
    PRINT_EMPTY_ROW = 0x84
    PRINT_CLEAR = 0x20
    PAGE_END = 0xE3
    PRINT_STATUS = 0xA3
    PRINT_END = 0xF3
    PRINTER_INFO = 0x40
    PRINTER_STATUS_DATA = 0xA5
    SET_DENSITY = 0x21
    SET_LABEL_TYPE = 0x23


class NiimbotResponse(IntEnum):
    NOT_SUPPORTED = 0x00
    CONNECT = 0xC2
    PRINT_START = 0x02
    PAGE_START = 0x04
    SET_PAGE_SIZE = 0x14
    PRINT_QUANTITY = 0x16
    PRINT_CLEAR = 0x30
    PAGE_END = 0xE4
    PRINTER_PAGE_INDEX = 0xE0
    PRINT_STATUS = 0xB3
    PRINT_END = 0xF4
    PRINT_ERROR = 0xDB
    PRINTER_INFO_MODEL_ID = 0x48
    PRINTER_STATUS_DATA = 0xB5
    SET_DENSITY = 0x31
    SET_LABEL_TYPE = 0x33


class NiimbotLabelType(IntEnum):
    WITH_GAPS = 1
    TRANSPARENT = 5


@dataclass(frozen=True)
class NiimbotPacket:
    command: int
    data: bytes


@dataclass(frozen=True)
class _NiimbotRowEncoder:
    printhead_pixels: int
    indexed_row_threshold: int = 6

    def build_steps(self, raster: RasterBuffer) -> tuple[ProtocolStep, ...]:
        rows = _coalesced_rows(raster)
        steps: list[ProtocolStep] = []
        for row in rows:
            for offset, repeat in _repeat_chunks(row.repeat):
                row_number = row.row_number + offset
                if row.row_data is None:
                    packet = frame(
                        NiimbotRequest.PRINT_EMPTY_ROW,
                        _u16be(row_number) + bytes([repeat]),
                    )
                elif row.black_pixels_count <= self.indexed_row_threshold:
                    packet = frame(
                        NiimbotRequest.PRINT_BITMAP_ROW_INDEXED,
                        _u16be(row_number)
                        + bytes(_count_pixels(row.row_data, self.printhead_pixels))
                        + bytes([repeat])
                        + _index_pixels(row.row_data),
                    )
                else:
                    packet = frame(
                        NiimbotRequest.PRINT_BITMAP_ROW,
                        _u16be(row_number)
                        + bytes(_count_pixels(row.row_data, self.printhead_pixels))
                        + bytes([repeat])
                        + row.row_data,
                    )
                steps.append(ProtocolStep.send(f"image row {row_number}", packet))
        return tuple(steps)


@dataclass(frozen=True)
class _NiimbotPrintTask(ABC):
    protocol_variant: str
    row_encoder: _NiimbotRowEncoder
    label_type: int = int(NiimbotLabelType.WITH_GAPS)
    packet_timeout_sec: float = 1.0
    page_timeout_sec: float = 10.0
    status_poll_interval_sec: float = 0.3
    status_timeout_sec: float = 5.0

    def build_job(self, request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
        if request.image_pipeline.encoding.value != "niimbot_d110":
            raise ValueError(
                f"Unsupported NIIMBOT image encoding: {request.image_pipeline.encoding.value}"
            )
        raster = request.require_raster(PixelFormat.BW1)
        raster.validate()
        if raster.width != self.row_encoder.printhead_pixels:
            raise ValueError(
                f"NIIMBOT {self.protocol_variant} jobs require "
                f"{self.row_encoder.printhead_pixels}px raster width"
            )
        density = request.density if request.density is not None else 2
        steps: list[ProtocolStep] = []
        if request.is_first_page:
            steps.extend(self._print_start_steps(density))
        steps.extend(self._page_steps(raster))
        steps.extend(self._completion_steps(request))
        if request.is_last_page:
            steps.append(
                self._query(
                    "print end",
                    frame(NiimbotRequest.PRINT_END),
                    NiimbotResponse.PRINT_END,
                    matcher=print_end_success_matcher(),
                )
            )
        return tuple(steps)

    def _print_start_steps(self, density: int) -> tuple[ProtocolStep, ...]:
        return (
            self._query(
                "set density",
                frame(NiimbotRequest.SET_DENSITY, bytes([density & 0xFF])),
                NiimbotResponse.SET_DENSITY,
            ),
            self._query(
                "set label type",
                frame(NiimbotRequest.SET_LABEL_TYPE, bytes([self.label_type & 0xFF])),
                NiimbotResponse.SET_LABEL_TYPE,
            ),
            self._query(
                "print start",
                frame(NiimbotRequest.PRINT_START),
                NiimbotResponse.PRINT_START,
            ),
        )

    def _page_steps(self, raster: RasterBuffer) -> tuple[ProtocolStep, ...]:
        steps = [
            self._query(
                "print clear",
                frame(NiimbotRequest.PRINT_CLEAR),
                NiimbotResponse.PRINT_CLEAR,
            ),
            self._query(
                "page start",
                frame(NiimbotRequest.PAGE_START),
                NiimbotResponse.PAGE_START,
            ),
            self._query(
                "set page size",
                frame(NiimbotRequest.SET_PAGE_SIZE, self._page_size_data(raster)),
                NiimbotResponse.SET_PAGE_SIZE,
                timeout_sec=self.page_timeout_sec,
            ),
            self._query(
                "print quantity",
                frame(NiimbotRequest.PRINT_QUANTITY, _u16be(1)),
                NiimbotResponse.PRINT_QUANTITY,
            ),
        ]
        steps.extend(self.row_encoder.build_steps(raster))
        steps.append(
            self._query(
                "page end",
                frame(NiimbotRequest.PAGE_END),
                NiimbotResponse.PAGE_END,
                timeout_sec=self.page_timeout_sec,
            )
        )
        return tuple(steps)

    @abstractmethod
    def _page_size_data(self, raster: RasterBuffer) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def _completion_steps(self, request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
        raise NotImplementedError

    def _query(
        self,
        label: str,
        packet: bytes,
        expected: NiimbotResponse,
        *,
        timeout_sec: float | None = None,
        matcher: ProtocolReplyMatcher | None = None,
    ) -> ProtocolStep:
        return ProtocolStep.query(
            label,
            packet,
            expect=ProtocolReplyExpectation.NONE,
            timeout_sec=self.packet_timeout_sec if timeout_sec is None else timeout_sec,
            reply_matcher=matcher or response_matcher(expected),
        )


class _D11V1PrintTask(_NiimbotPrintTask):
    def _page_size_data(self, raster: RasterBuffer) -> bytes:
        return _u16be(raster.height)

    def _completion_steps(self, request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
        if not request.is_last_page:
            return ()
        # TODO: this mirrors the source D11_V1 flow: wait for page-index after
        # PageEnd. If hardware shows the notification can arrive before this
        # waiter is registered, add a NIIMBOT notification accumulator instead
        # of moving this logic into transport.
        return (
            ProtocolStep.wait(
                "page index",
                reply_matcher=page_index_done_matcher(request.page_count),
                timeout_sec=self.status_timeout_sec,
            ),
        )


class _D110PrintTask(_NiimbotPrintTask):
    def _page_size_data(self, raster: RasterBuffer) -> bytes:
        return _u16be(raster.height) + _u16be(raster.width)

    def _completion_steps(self, request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
        return (
            ProtocolStep.query(
                "print status",
                frame(NiimbotRequest.PRINT_STATUS),
                expect=ProtocolReplyExpectation.NONE,
                timeout_sec=self.packet_timeout_sec,
                reply_matcher=print_status_done_matcher(request.page_index),
                repeat_interval_sec=self.status_poll_interval_sec,
                repeat_timeout_sec=self.status_timeout_sec,
                include_in_payload=False,
            ),
        )


def frame(command: int | NiimbotRequest, data: bytes = b"\x01") -> bytes:
    command_int = int(command) & 0xFF
    payload = bytes(data)
    checksum = command_int ^ len(payload)
    for value in payload:
        checksum ^= value
    packet = (
        b"\x55\x55"
        + bytes([command_int, len(payload)])
        + payload
        + bytes([checksum & 0xFF])
        + b"\xAA\xAA"
    )
    if command_int == int(NiimbotRequest.CONNECT):
        return b"\x03" + packet
    return packet


def parse_packets(raw: bytes) -> tuple[NiimbotPacket, ...]:
    data = bytes(raw)
    packets: list[NiimbotPacket] = []
    offset = 0
    while offset < len(data):
        if data[offset : offset + 2] != b"\x55\x55":
            raise ValueError("Invalid NIIMBOT packet head")
        if offset + 6 > len(data):
            raise ValueError("Incomplete NIIMBOT packet")
        command = data[offset + 2]
        length = data[offset + 3]
        payload_start = offset + 4
        checksum_index = payload_start + length
        tail_start = checksum_index + 1
        tail_end = tail_start + 2
        if tail_end > len(data):
            raise ValueError("Incomplete NIIMBOT packet")
        if data[tail_start:tail_end] != b"\xAA\xAA":
            raise ValueError("Invalid NIIMBOT packet tail")
        payload = data[payload_start:checksum_index]
        if _checksum(command, payload) != data[checksum_index]:
            raise ValueError("Invalid NIIMBOT packet checksum")
        packets.append(NiimbotPacket(command=command, data=payload))
        offset = tail_end
    return tuple(packets)


def response_matcher(
    expected: NiimbotResponse | Iterable[NiimbotResponse],
    *,
    data_matches: Callable[[bytes], bool] | None = None,
) -> ProtocolReplyMatcher:
    expected_ids = _response_ids(expected)

    def complete(raw: bytes) -> bool:
        return any(packet.command in expected_ids for packet in _safe_parse_packets(raw))

    def matches(raw: bytes | None) -> bool:
        if raw is None:
            return False
        for packet in _safe_parse_packets(raw):
            if packet.command not in expected_ids:
                continue
            return True if data_matches is None else data_matches(packet.data)
        return False

    return ProtocolReplyMatcher(complete=complete, matches=matches)


def print_status_done_matcher(expected_page: int) -> ProtocolReplyMatcher:
    expected_pages = max(1, int(expected_page))

    def status_done(payload: bytes) -> bool:
        return len(payload) >= 2 and int.from_bytes(payload[:2], "big") >= expected_pages

    return response_matcher(NiimbotResponse.PRINT_STATUS, data_matches=status_done)


def page_index_done_matcher(expected_page: int) -> ProtocolReplyMatcher:
    expected_pages = max(1, int(expected_page))

    def page_done(raw: bytes | None) -> bool:
        if raw is None:
            return False
        for packet in _safe_parse_packets(raw):
            if packet.command != int(NiimbotResponse.PRINTER_PAGE_INDEX):
                continue
            if len(packet.data) >= 2 and int.from_bytes(packet.data[:2], "big") == expected_pages:
                return True
        return False

    return ProtocolReplyMatcher(complete=lambda raw: page_done(raw), matches=page_done)


def print_end_success_matcher() -> ProtocolReplyMatcher:
    return response_matcher(
        NiimbotResponse.PRINT_END,
        data_matches=lambda payload: bool(payload and payload[0] == 1),
    )


def build_niimbot_job(request: PrintJobRequest) -> tuple[ProtocolStep, ...]:
    print_task = PRINT_TASK_BY_VARIANT.get(request.protocol_variant or "d110")
    if print_task is None:
        raise ValueError(f"Unsupported NIIMBOT protocol variant: {request.protocol_variant!r}")
    return print_task.build_job(request)


def connect_packet() -> bytes:
    return frame(NiimbotRequest.CONNECT)


def model_id_query_packet() -> bytes:
    return frame(NiimbotRequest.PRINTER_INFO, bytes([0x08]))


def status_data_query_packet() -> bytes:
    return frame(NiimbotRequest.PRINTER_STATUS_DATA)


def model_id_from_reply(raw: bytes | None) -> int | None:
    if raw is None:
        return None
    for packet in _safe_parse_packets(raw):
        if packet.command != int(NiimbotResponse.PRINTER_INFO_MODEL_ID):
            continue
        if len(packet.data) == 1:
            return packet.data[0] << 8
        if len(packet.data) == 2:
            return int.from_bytes(packet.data, "big", signed=True)
    return None


def protocol_version_from_status_data(raw: bytes | None) -> int | None:
    if raw is None:
        return None
    for packet in _safe_parse_packets(raw):
        if packet.command != int(NiimbotResponse.PRINTER_STATUS_DATA):
            continue
        if len(packet.data) <= 12:
            return 0
        encoded = packet.data[11] * 100 + packet.data[12]
        if 204 <= encoded < 300:
            return 3
        if encoded >= 302:
            return 5
        if encoded == 300 or encoded == 301:
            return 4
        return 0
    return None


@dataclass(frozen=True)
class _EncodedRow:
    row_number: int
    repeat: int
    black_pixels_count: int
    row_data: bytes | None


def _coalesced_rows(raster: RasterBuffer) -> tuple[_EncodedRow, ...]:
    if raster.pixel_format is not PixelFormat.BW1:
        raise ValueError("NIIMBOT requires BW1 raster data")
    if raster.width % 8 != 0:
        raise ValueError("NIIMBOT raster width must be divisible by 8")
    rows: list[_EncodedRow] = []
    pixels = list(raster.pixels)
    for row_number in range(raster.height):
        line = pixels[row_number * raster.width : (row_number + 1) * raster.width]
        black_pixels_count = sum(1 for pixel in line if pixel)
        row_data = None if black_pixels_count == 0 else pack_line(line, lsb_first=False)
        new_row = _EncodedRow(
            row_number=row_number,
            repeat=1,
            black_pixels_count=black_pixels_count,
            row_data=row_data,
        )
        if rows and _same_row(rows[-1], new_row):
            previous = rows[-1]
            rows[-1] = _EncodedRow(
                row_number=previous.row_number,
                repeat=previous.repeat + 1,
                black_pixels_count=previous.black_pixels_count,
                row_data=previous.row_data,
            )
        else:
            rows.append(new_row)
    return tuple(rows)


def _same_row(left: _EncodedRow, right: _EncodedRow) -> bool:
    return left.row_data == right.row_data


def _repeat_chunks(repeat: int) -> tuple[tuple[int, int], ...]:
    chunks = []
    remaining = max(0, int(repeat))
    offset = 0
    while remaining > 0:
        chunk = min(255, remaining)
        chunks.append((offset, chunk))
        offset += chunk
        remaining -= chunk
    return tuple(chunks)


def _count_pixels(row_data: bytes, printhead_pixels: int) -> tuple[int, int, int]:
    chunk_size = printhead_pixels // 8 // 3
    split = chunk_size > 0 and len(row_data) <= chunk_size * 3
    total = 0
    parts = [0, 0, 0]
    for byte_index, value in enumerate(row_data):
        chunk_index = byte_index // chunk_size if chunk_size else 0
        bit_count = _BIT_COUNTS[value]
        total += bit_count
        if split and chunk_index < 3:
            parts[chunk_index] += bit_count
    if split:
        return parts[0], parts[1], parts[2]
    return (0, total & 0xFF, (total >> 8) & 0xFF)


def _index_pixels(row_data: bytes) -> bytes:
    indexes = bytearray()
    for byte_pos, value in enumerate(row_data):
        for bit_pos in range(8):
            if value & (1 << (7 - bit_pos)):
                indexes += _u16be(byte_pos * 8 + bit_pos)
    return bytes(indexes)


def _response_ids(expected: NiimbotResponse | Iterable[NiimbotResponse]) -> frozenset[int]:
    if isinstance(expected, NiimbotResponse):
        return frozenset({int(expected)})
    return frozenset(int(value) for value in expected)


def _safe_parse_packets(raw: bytes) -> tuple[NiimbotPacket, ...]:
    try:
        return parse_packets(raw)
    except ValueError:
        return ()


def _checksum(command: int, payload: bytes) -> int:
    value = command ^ len(payload)
    for item in payload:
        value ^= item
    return value & 0xFF


def _u16be(value: int) -> bytes:
    return int(value).to_bytes(2, "big", signed=False)


_SHARED_96PX_ROW_ENCODER = _NiimbotRowEncoder(printhead_pixels=96)

D11_PRINT_TASK = _D11V1PrintTask(
    protocol_variant="d11_v1",
    row_encoder=_SHARED_96PX_ROW_ENCODER,
)
D110_PRINT_TASK = _D110PrintTask(
    protocol_variant="d110",
    row_encoder=_SHARED_96PX_ROW_ENCODER,
)
PRINT_TASK_BY_VARIANT = {
    D11_PRINT_TASK.protocol_variant: D11_PRINT_TASK,
    D110_PRINT_TASK.protocol_variant: D110_PRINT_TASK,
}
