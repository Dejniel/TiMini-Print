from __future__ import annotations

import zlib

from ._lzo import compress_lzo1x_1


def compress_zlib(data: bytes, *, window_bits: int = 10) -> bytes:
    """Encode a zlib-framed deflate stream with the selected window size.

    Raster protocols using this codec use ``memLevel=8``,
    ``strategy=Z_DEFAULT_STRATEGY``, and compression level ``6``.
    """

    try:
        compressor = zlib.compressobj(
            level=6,
            method=zlib.DEFLATED,
            wbits=window_bits,
            memLevel=8,
            strategy=zlib.Z_DEFAULT_STRATEGY,
        )
        return compressor.compress(data) + compressor.flush()
    except Exception as exc:  # pragma: no cover - built-in zlib should not fail
        raise RuntimeError("zlib raster compression failed") from exc
