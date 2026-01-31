from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Any, Dict, Iterable, Optional, Set

STATUS_IDLE = "idle"
STATUS_SCAN_START = "scan_start"
STATUS_SCAN_DONE = "scan_done"
STATUS_CONNECT_START = "connect_start"
STATUS_PAIRING_CONFIRM = "pairing_confirm"
STATUS_CONNECT_DONE = "connect_done"
STATUS_DISCONNECT_START = "disconnect_start"
STATUS_DISCONNECT_DONE = "disconnect_done"
STATUS_PRINTING = "printing"
STATUS_PRINT_SENT = "print_sent"
STATUS_PAPER_FEED = "paper_feed"
STATUS_PAPER_RETRACT = "paper_retract"

WARNING_MODEL_ALIAS = "model_alias"
WARNING_DEPENDENCY = "dependency_missing"
WARNING_SCAN_CLASSIC_FAILED = "scan_classic_failed"
WARNING_SCAN_BLE_FAILED = "scan_ble_failed"

ERROR_SCAN_FAILED = "scan_failed"
ERROR_CONNECT_FAILED = "connect_failed"
ERROR_DISCONNECT_FAILED = "disconnect_failed"
ERROR_PRINT_FAILED = "print_failed"
ERROR_PAPER_MOTION_FAILED = "paper_motion_failed"
ERROR_NO_DEVICE = "no_device"
ERROR_NO_FILE = "no_file"
ERROR_MODEL_NOT_DETECTED = "model_not_detected"


class MessageCatalog:
    STATUS = {
        STATUS_IDLE: "Idle",
        STATUS_SCAN_START: "Refreshing devices...",
        STATUS_SCAN_DONE: "Found {count} devices",
        STATUS_CONNECT_START: "Connecting...",
        STATUS_PAIRING_CONFIRM: "Pairing... confirm in Windows popup",
        STATUS_CONNECT_DONE: "Connected",
        STATUS_DISCONNECT_START: "Disconnecting...",
        STATUS_DISCONNECT_DONE: "Disconnected",
        STATUS_PRINTING: "Printing...",
        STATUS_PRINT_SENT: "Print job sent",
        STATUS_PAPER_FEED: "Feeding paper...",
        STATUS_PAPER_RETRACT: "Retracting paper...",
    }
    WARNING = {
        WARNING_MODEL_ALIAS: "Model detected via alias",
        WARNING_DEPENDENCY: "Missing dependency",
        WARNING_SCAN_CLASSIC_FAILED: "Classic scan failed",
        WARNING_SCAN_BLE_FAILED: "BLE scan failed",
    }
    ERROR = {
        ERROR_SCAN_FAILED: "Scan failed",
        ERROR_CONNECT_FAILED: "Connection failed",
        ERROR_DISCONNECT_FAILED: "Disconnect failed",
        ERROR_PRINT_FAILED: "Print failed",
        ERROR_PAPER_MOTION_FAILED: "Paper motion failed",
        ERROR_NO_DEVICE: "Select a Bluetooth device",
        ERROR_NO_FILE: "Select a file to print",
        ERROR_MODEL_NOT_DETECTED: "Printer model not detected",
    }

    @classmethod
    def resolve(cls, level: str, key: Optional[str], **ctx: Any) -> Optional[str]:
        if not key:
            return None
        if level == "status":
            mapping = cls.STATUS
        elif level == "warning":
            mapping = cls.WARNING
        else:
            mapping = cls.ERROR
        template = mapping.get(key)
        if not template:
            return None
        try:
            return template.format(**ctx)
        except Exception:
            return template


def summarize_detail(detail: str) -> str:
    text = (detail or "").strip()
    if not text:
        return ""
    for sep in (".", ";"):
        idx = text.find(sep)
        if 0 < idx <= 80:
            return text[:idx].strip()
    if " (" in text and len(text) > 60:
        return text.split(" (", 1)[0].strip()
    if len(text) > 80:
        return text[:77].rstrip() + "..."
    return text


@dataclass(frozen=True)
class ReportMessage:
    level: str
    key: Optional[str]
    short: str
    detail: Optional[str] = None
    exc: Optional[Exception] = None
    context: Dict[str, Any] = field(default_factory=dict)


class ReportSink:
    def emit(self, message: ReportMessage) -> None:
        raise NotImplementedError


class StderrSink(ReportSink):
    def __init__(
        self,
        *,
        stream=None,
        levels: Optional[Iterable[str]] = None,
        prefix_levels: Optional[Iterable[str]] = None,
    ) -> None:
        self._stream = stream or sys.stderr
        self._levels: Set[str] = set(levels or {"warning", "error"})
        if prefix_levels is None:
            prefix_levels = {"warning", "error"}
        self._prefix_levels: Set[str] = set(prefix_levels)

    def emit(self, message: ReportMessage) -> None:
        if message.level not in self._levels:
            return
        text = message.detail or message.short
        if not text:
            return
        prefix = ""
        if message.level in self._prefix_levels:
            prefix = "Warning: " if message.level == "warning" else "Error: "
        print(f"{prefix}{text}", file=self._stream)


class QueueStatusSink(ReportSink):
    def __init__(self, queue, *, show_warnings: bool = True) -> None:
        self._queue = queue
        self._show_warnings = show_warnings

    def emit(self, message: ReportMessage) -> None:
        if message.level == "status":
            if message.short:
                self._queue.put(("status", message.short))
            return
        if message.level == "error":
            if message.short:
                self._queue.put(("error", message.short))
            return
        if message.level == "warning" and self._show_warnings:
            if message.short:
                self._queue.put(("status", f"Warning: {message.short}"))


class Reporter:
    def __init__(self, sinks: Iterable[ReportSink], *, catalog: MessageCatalog = MessageCatalog()) -> None:
        self._sinks = list(sinks)
        self._catalog = catalog

    def status(self, key: Optional[str] = None, *, short: Optional[str] = None, detail: Optional[str] = None, **ctx: Any) -> None:
        self._emit("status", key, short, detail, None, ctx)

    def warning(
        self,
        key: Optional[str] = None,
        *,
        short: Optional[str] = None,
        detail: Optional[str] = None,
        exc: Optional[Exception] = None,
        **ctx: Any,
    ) -> None:
        self._emit("warning", key, short, detail, exc, ctx)

    def error(
        self,
        key: Optional[str] = None,
        *,
        short: Optional[str] = None,
        detail: Optional[str] = None,
        exc: Optional[Exception] = None,
        **ctx: Any,
    ) -> None:
        self._emit("error", key, short, detail, exc, ctx)

    def _emit(
        self,
        level: str,
        key: Optional[str],
        short: Optional[str],
        detail: Optional[str],
        exc: Optional[Exception],
        ctx: Dict[str, Any],
    ) -> None:
        if detail is None and exc is not None:
            detail = str(exc)
        if short is None:
            short = self._catalog.resolve(level, key, **ctx)
        if short is None and detail:
            short = summarize_detail(detail)
        if short is None:
            short = key or ""
        message = ReportMessage(
            level=level,
            key=key,
            short=short,
            detail=detail,
            exc=exc,
            context=dict(ctx),
        )
        for sink in self._sinks:
            sink.emit(message)
