from __future__ import annotations

import zlib

from ._lzo import compress_lzo1x_1


def compress_zlib(
    data: bytes,
    *,
    window_bits: int = 10,
    level: int = 6,
    memory_level: int = 8,
) -> bytes:
    """Encode a zlib-framed deflate stream with the selected window size.

    Existing protocols use the defaults. Documented variants may select a
    different compression or memory level without duplicating this codec.
    """

    try:
        compressor = zlib.compressobj(
            level=level,
            method=zlib.DEFLATED,
            wbits=window_bits,
            memLevel=memory_level,
            strategy=zlib.Z_DEFAULT_STRATEGY,
        )
        return compressor.compress(data) + compressor.flush()
    except Exception as exc:  # pragma: no cover - built-in zlib should not fail
        raise RuntimeError("zlib raster compression failed") from exc
