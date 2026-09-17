"""Transport-independent codes for conditions reported by a printer."""

from enum import Enum


class PrinterStatusCode(str, Enum):
    """Stable reason codes, not a universal interpretation of status bytes.

    Each family decodes its own replies. ``NOT_READY`` and ``PRINTER_ERROR``
    retain an unspecified refusal/fault without guessing a physical cause.
    Missing replies and communication timeouts are not printer conditions.
    """

    PAPER_OUT = "paper_out"
    COVER_OPEN = "cover_open"
    OVERHEATED = "overheated"
    LOW_BATTERY = "low_battery"
    BUSY = "busy"
    RIBBON_ERROR = "ribbon_error"
    CUTTER_ERROR = "cutter_error"
    NOT_READY = "not_ready"
    PRINTER_ERROR = "printer_error"
