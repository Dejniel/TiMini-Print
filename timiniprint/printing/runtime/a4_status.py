"""Status bytes shared by the direct A4 command dialects."""

from __future__ import annotations

from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError


def query_status_error(
    reply: bytes | None, *, device_label: str, extra_overheat_mask: int = 0,
    ignored_mask: int = 0,
) -> PrinterNotReadyError | None:
    """Classify the first status byte after validating the dialect's reply shape."""
    if not reply:
        return None
    flags = reply[0] & ~ignored_mask
    errors = [(code, text) for mask, code, text in (
        (2, PrinterStatusCode.COVER_OPEN, "cover open"),
        (4, PrinterStatusCode.PAPER_OUT, "out of paper"),
        (8, PrinterStatusCode.LOW_BATTERY, "low battery"),
        (16 | extra_overheat_mask, PrinterStatusCode.OVERHEATED, "overheat"),
        (1, PrinterStatusCode.BUSY, "printing"),
    ) if flags & mask]
    if not flags:
        return None
    if not errors:
        errors = [(PrinterStatusCode.NOT_READY, f"unknown status 0x{flags:02x}")]
    return PrinterNotReadyError(
        f"{device_label}: {', '.join(text for _, text in errors)}",
        *(code for code, _ in errors),
    )
