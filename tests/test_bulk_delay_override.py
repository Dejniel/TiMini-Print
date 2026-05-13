"""Tests for the TIMINI_BLE_BULK_DELAY_MS environment override."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from timiniprint.transport.bluetooth.adapters.bleak_adapter_transport import (
    _resolve_bulk_delay_ms,
)


class ResolveBulkDelayTests(unittest.TestCase):
    def test_unset_returns_profile_value(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TIMINI_BLE_BULK_DELAY_MS", None)
            self.assertEqual(_resolve_bulk_delay_ms(10), 10)

    def test_valid_override_replaces_profile_value(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_BLE_BULK_DELAY_MS": "30"}):
            self.assertEqual(_resolve_bulk_delay_ms(10), 30)

    def test_zero_override_is_honoured(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_BLE_BULK_DELAY_MS": "0"}):
            self.assertEqual(_resolve_bulk_delay_ms(10), 0)

    def test_non_numeric_falls_back_to_profile_value(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_BLE_BULK_DELAY_MS": "fast"}):
            self.assertEqual(_resolve_bulk_delay_ms(10), 10)

    def test_negative_falls_back_to_profile_value(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_BLE_BULK_DELAY_MS": "-5"}):
            self.assertEqual(_resolve_bulk_delay_ms(10), 10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
