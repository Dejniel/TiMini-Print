"""Display-safe failures while reading or decoding document data."""

from __future__ import annotations

import os


class DocumentReadError(Exception):
    """Read/decode failure without source names or native exception text.

    Decoders may chain their original exception. DocumentRenderer records it
    at trace level and suppresses that chain before exposing this error.
    """

    def __init__(self, kind: str | None, cause: Exception) -> None:
        reason = type(cause).__name__
        if isinstance(cause, OSError) and cause.errno is not None:
            reason = os.strerror(cause.errno)
        super().__init__(f"Cannot read {kind or 'input'} document: {reason}")
