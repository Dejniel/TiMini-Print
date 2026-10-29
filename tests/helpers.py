from __future__ import annotations

import json
import importlib
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import List

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog


class CaptureSink(reporting.ReportSink):
    def __init__(self) -> None:
        self.messages: List[reporting.ReportMessage] = []

    def emit(self, message: reporting.ReportMessage) -> None:
        self.messages.append(message)


def build_capture_reporter(
    *, min_level: reporting.ReportLevel = reporting.ReportLevel.DEBUG,
) -> tuple[reporting.Reporter, CaptureSink]:
    sink = CaptureSink()
    return reporting.Reporter([sink], min_level=min_level), sink


def reset_registry_cache() -> None:
    PrinterCatalog._cache = {}


def catalog_for_sources(*origin_ids: str) -> PrinterCatalog:
    """Test a source's recipes independently; collision tests use the full catalog."""
    catalog = PrinterCatalog.load()
    sources = set(origin_ids)
    models = [
        replace(model, origin_ids=tuple(origin for origin in model.origin_ids if origin in sources))
        for model in catalog.models if sources.intersection(model.origin_ids)
    ]
    origins = {origin for model in models for origin in model.origin_ids}
    return PrinterCatalog(catalog.profiles, models, origin_names={
        origin: catalog.origin_names((origin,))[0] for origin in origins
    })


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
