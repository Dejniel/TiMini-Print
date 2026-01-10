from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import List

from ..transport.bluetooth.constants import IS_LINUX, IS_WINDOWS

_WARNED = False
_REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "requirements.txt"


def emit_startup_warnings() -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    for message in collect_dependency_warnings():
        print(f"Warning: {message}", file=sys.stderr)


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
                warnings.append("Missing Pillow (PIL). Image/PDF/text rendering will not work.")
        elif requirement == "crc8":
            if not _has_module("crc8"):
                warnings.append("Missing crc8. Printer protocol encoding will not work.")
        elif requirement == "bleak":
            if IS_LINUX and not _has_module("bleak"):
                if shutil.which("bluetoothctl"):
                    warnings.append("Missing bleak. Bluetooth scanning will use bluetoothctl only.")
                else:
                    warnings.append("Missing bleak and bluetoothctl. Bluetooth scanning will not work.")
        elif requirement == "pyserial":
            if not _has_module("serial"):
                warnings.append("Missing pyserial. Serial printing via --serial will not work.")
        elif requirement == "winsdk":
            if IS_WINDOWS and not _has_module("winsdk"):
                warnings.append("Missing winsdk. Windows Bluetooth SPP scanning/connection will not work.")
        elif not _has_module(requirement):
            warnings.append(f"Missing dependency: {requirement}.")
    if _missing_pdf_backends():
        warnings.append(
            "PDF rendering backend not found. Install PyMuPDF (pymupdf), pdf2image + poppler, or system pdftoppm."
        )
    return warnings


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _missing_pdf_backends() -> bool:
    if _has_module("fitz"):
        return False
    if _has_module("pdf2image"):
        return False
    if shutil.which("pdftoppm"):
        return False
    return True
