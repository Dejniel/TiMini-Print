from __future__ import annotations

import json
import importlib
import sys
import types
from pathlib import Path
from typing import List

from timiniprint import reporting
from timiniprint.devices.models import PrinterModelRegistry


class CaptureSink(reporting.ReportSink):
    def __init__(self) -> None:
        self.messages: List[reporting.ReportMessage] = []

    def emit(self, message: reporting.ReportMessage) -> None:
        self.messages.append(message)


def build_capture_reporter() -> tuple[reporting.Reporter, CaptureSink]:
    sink = CaptureSink()
    return reporting.Reporter([sink]), sink


def reset_registry_cache() -> None:
    PrinterModelRegistry._cache = {}


def reset_adapter_cache() -> None:
    adapters_init = importlib.import_module("timiniprint.transport.bluetooth.adapters.__init__")
    adapters_init._CLASSIC_ADAPTER = None
    adapters_init._BLE_ADAPTER = None


def install_crc8_stub(force: bool = False) -> None:
    if not force and "crc8" in sys.modules:
        return
    module = types.ModuleType("crc8")

    class _CRC8:
        def __init__(self) -> None:
            self._crc = 0

        def update(self, data: bytes) -> None:
            for b in data:
                self._crc ^= b
                for _ in range(8):
                    if self._crc & 0x80:
                        self._crc = ((self._crc << 1) ^ 0x07) & 0xFF
                    else:
                        self._crc = (self._crc << 1) & 0xFF

        def digest(self) -> bytes:
            return bytes([self._crc])

    module.crc8 = _CRC8
    sys.modules["crc8"] = module


def load_golden_hex(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
