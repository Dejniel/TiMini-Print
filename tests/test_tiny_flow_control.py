from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.devices import PrinterCatalog, get_ble_transport_profile
from timiniprint.devices.profiles import RuntimeSettings
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.tiny import TinyRuntimeController
from timiniprint.protocol.families.tiny import (
    TINY_FLOW_PAUSE_PACKET,
    TINY_FLOW_RESUME_PACKET,
)
from timiniprint.protocol.family import ProtocolFamily


class _RecordingSession:
    """Minimal session that records the flow decisions a controller makes."""

    def __init__(self) -> None:
        self.flow_calls: list[bool] = []
        self.debug: list[str] = []

    def set_flow_paused(self, paused: bool, *, payload: bytes = b"") -> None:
        self.flow_calls.append(paused)

    def report_debug(self, message: str) -> None:
        self.debug.append(message)

    def can_wait_for_notification(self) -> bool:
        return False


def _tiny_device(control_algorithm: str | None):
    device = PrinterCatalog.load().device_from_profile("x5_2")
    if control_algorithm is None:
        return device
    return replace(
        device,
        runtime_settings=RuntimeSettings(control_algorithm=control_algorithm),
    )


class TinyFlowControlTests(unittest.TestCase):
    def test_pause_and_resume_frames_gate_the_write_loop(self) -> None:
        controller = TinyRuntimeController()
        session = _RecordingSession()

        controller.handle_notification(session, TINY_FLOW_PAUSE_PACKET)
        controller.handle_notification(session, TINY_FLOW_RESUME_PACKET)

        self.assertEqual(session.flow_calls, [True, False])

    def test_unrelated_notification_leaves_the_flow_alone(self) -> None:
        controller = TinyRuntimeController()
        session = _RecordingSession()

        controller.handle_notification(session, bytes.fromhex("5178A30101002233FF"))

        self.assertEqual(session.flow_calls, [])

    def test_controller_is_opt_in_per_device(self) -> None:
        # Without an opt-in a tiny device must keep the old behaviour: no
        # controller, so nothing waits for frames the hardware may never send.
        self.assertIsNone(runtime_controller_for_device(_tiny_device(None)))
        self.assertIsInstance(
            runtime_controller_for_device(_tiny_device("x5_poll")),
            TinyRuntimeController,
        )

    def test_transport_profile_is_opt_in_per_control_algorithm(self) -> None:
        plain = get_ble_transport_profile(ProtocolFamily.TINY)
        self.assertFalse(plain.prefer_generic_notify)
        self.assertFalse(plain.flow_controlled_standard_write)
        self.assertIsNone(plain.flow_resume_timeout_s)

        polling = get_ble_transport_profile(ProtocolFamily.TINY, "x5_poll")
        self.assertTrue(polling.prefer_generic_notify)
        self.assertTrue(polling.flow_controlled_standard_write)
        # Hardware sent the resume frame 30-36s after pausing, so the budget has
        # to outlast the send timeout by a wide margin.
        self.assertIsNotNone(polling.flow_resume_timeout_s)
        assert polling.flow_resume_timeout_s is not None
        self.assertGreater(polling.flow_resume_timeout_s, 60.0)

    def test_unknown_control_algorithm_falls_back_to_the_family(self) -> None:
        fallback = get_ble_transport_profile(ProtocolFamily.TINY, "not_a_known_algorithm")
        self.assertEqual(fallback, get_ble_transport_profile(ProtocolFamily.TINY))


if __name__ == "__main__":
    unittest.main()
