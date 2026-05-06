from __future__ import annotations

import zlib

try:
    import lzo as _lzo
except ImportError as exc:  # pragma: no cover - import is validated in callers
    _lzo = None
    _LZO_IMPORT_ERROR = exc
else:
    _LZO_IMPORT_ERROR = None


def compress_lzo1x_1(data: bytes) -> bytes:
    if _lzo is None:
        raise RuntimeError("python-lzo is required for V5C compressed jobs") from _LZO_IMPORT_ERROR

    compress = getattr(_lzo, "compress", None)
    if compress is None:
        raise RuntimeError("python-lzo does not expose lzo.compress()")

    last_signature_error: TypeError | None = None
    for args in ((data, 1, False), (data, 1), (data,)):
        try:
            result = compress(*args)
        except TypeError as exc:
            last_signature_error = exc
            continue
        except Exception as exc:
            raise RuntimeError("LZO compression failed for V5C compressed job") from exc
        return bytes(result)

    raise RuntimeError(
        "python-lzo compress() did not accept any supported LZO1X-1 call signature"
    ) from last_signature_error


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
