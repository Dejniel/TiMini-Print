from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.tiny import TinyRuntimeController
from timiniprint.protocol.families.tiny import (
    TINY_NOTIFY_PAUSE,
    TINY_NOTIFY_RESUME,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.flow_calls: list[tuple[bool, bytes]] = []

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        self.flow_calls.append((paused, payload))


class TinyFlowControlTests(unittest.TestCase):
    def test_runtime_handles_fragmented_and_coalesced_notifications(self) -> None:
        controller = TinyRuntimeController()
        session = _RecordingSession()

        controller.handle_notification(session, TINY_NOTIFY_PAUSE[:4])
        controller.handle_notification(
            session,
            TINY_NOTIFY_PAUSE[4:] + TINY_NOTIFY_RESUME,
        )

        self.assertEqual(
            session.flow_calls,
            [
                (True, TINY_NOTIFY_PAUSE),
                (False, TINY_NOTIFY_RESUME),
            ],
        )

    def test_runtime_ignores_unrelated_tiny_notification(self) -> None:
        controller = TinyRuntimeController()
        session = _RecordingSession()

        controller.handle_notification(
            session,
            bytes.fromhex("5178A30101000000FF"),
        )

        self.assertEqual(session.flow_calls, [])

    def test_runtime_is_enabled_for_both_tiny_prefix_variants(self) -> None:
        catalog = PrinterCatalog.load()
        for model_key in ("x5", "lp100"):
            with self.subTest(model_key=model_key):
                device = catalog.device_from_model(model_key)
                self.assertIsInstance(
                    runtime_controller_for_device(device),
                    TinyRuntimeController,
                )


if __name__ == "__main__":
    unittest.main()
