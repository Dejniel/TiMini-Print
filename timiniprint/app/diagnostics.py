from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

from ..transport.bluetooth.constants import IS_WINDOWS
from .. import reporting

_WARNED = False
_REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "requirements.txt"


def emit_startup_warnings(reporter: Optional[reporting.Reporter] = None) -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    if reporter is None:
        reporter = reporting.Reporter([reporting.StderrSink()])
    for message in collect_dependency_warnings():
        reporter.warning(
            reporting.WARNING_DEPENDENCY,
            short=reporting.summarize_detail(message),
            detail=message,
        )


def collect_dependency_warnings() -> List[str]:
    try:
        lines = _REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        requirements = [l.split(";", 1)[0].split("#", 1)[0].split("[", 1)[0].strip().split("=", 1)[0].split(">", 1)[0].split("<", 1)[0].lower() for l in (line.strip() for line in lines) if l and not l.startswith(("#", "-")) and ("sys_platform" not in l or ("==" in l and sys.platform == l.split("==", 1)[1].split()[0].strip("'\"")) or ("!=" in l and sys.platform != l.split("!=", 1)[1].split()[0].strip("'\"")))]
    except OSError:
        requirements = []
    warnings: List[str] = []
    for requirement in requirements:
        if requirement == "pillow":
            if not _has_module("PIL"):
                warnings.append("Missing Pillow (PIL). Image/text rendering will not work, and PDF raster output will fail.")
        elif requirement == "pypdfium2":
            if not _has_module("pypdfium2"):
                warnings.append("Missing pypdfium2. PDF rendering will not work.")
        elif requirement == "crc8":
            if not _has_module("crc8"):
                warnings.append("Missing crc8. Printer protocol encoding will not work.")
        elif requirement == "bleak":
            if not _has_module("bleak"):
                warnings.append("Missing bleak. BLE scanning/printing will not work.")
        elif requirement == "pyserial":
            if not _has_module("serial"):
                warnings.append("Missing pyserial. Serial printing via --serial will not work.")
        elif requirement == "winsdk":
            if IS_WINDOWS and not _has_module("winsdk"):
                warnings.append("Missing winsdk. Windows Bluetooth SPP scanning/connection will not work.")
        elif not _has_module(requirement):
            warnings.append(f"Missing dependency: {requirement}.")
    return warnings


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
