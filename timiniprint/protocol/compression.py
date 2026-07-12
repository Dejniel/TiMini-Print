from __future__ import annotations

import zlib

from ._lzo import compress_lzo1x_1


def compress_zlib_wbits_10(data: bytes) -> bytes:
    """Encode a zlib-framed deflate stream with ``windowBits=10``.

    This compressed bitmap path uses ``memLevel=8``,
    ``strategy=Z_DEFAULT_STRATEGY``, and the default level ``6``.
    """

    try:
        compressor = zlib.compressobj(
            level=6,
            method=zlib.DEFLATED,
            wbits=10,
            memLevel=8,
            strategy=zlib.Z_DEFAULT_STRATEGY,
        )
        return compressor.compress(data) + compressor.flush()
    except Exception as exc:  # pragma: no cover - built-in zlib should not fail
        raise RuntimeError("zlib compression failed for Luck normal compressed job") from exc
