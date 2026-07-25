from __future__ import annotations

from ...raster import RasterBuffer
from ..compression import compress_lzo1x_1
from ..family import ProtocolFamily
from ..packet import make_packet


def build_lzo_band_frames(
    raster: RasterBuffer,
    *,
    opcode: int,
    rows_per_band: int,
    protocol_family: ProtocolFamily,
) -> bytes:
    if rows_per_band <= 0:
        raise ValueError("V5 LZO band height must be positive")
    frames = bytearray()
    for row in range(0, raster.height, rows_per_band):
        rows = min(rows_per_band, raster.height - row)
        raw_block = raster.slice_rows(row, rows).packed_bytes()
        compressed = compress_lzo1x_1(raw_block)
        payload = (
            len(raw_block).to_bytes(2, "little")
            + len(compressed).to_bytes(2, "little")
            + compressed
        )
        frames += make_packet(opcode, payload, protocol_family)
    return bytes(frames)


__all__ = ["build_lzo_band_frames"]
