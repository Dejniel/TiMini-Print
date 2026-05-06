from __future__ import annotations

import unittest

from timiniprint import reporting
from timiniprint.app.gui import TiMiniPrintGUI
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition


class GuiPaperMotionStatusTests(unittest.TestCase):
    def test_restore_status_after_paper_motion_uses_connected_when_connected(self) -> None:
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        seen: list[str] = []
        gui.connected_device = object()
        gui._queue_status = lambda key, **ctx: seen.append(key)

        gui._restore_status_after_paper_motion()

        self.assertEqual(seen, [reporting.STATUS_CONNECT_DONE])

    def test_restore_status_after_paper_motion_uses_idle_when_disconnected(self) -> None:
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        seen: list[str] = []
        gui.connected_device = None
        gui._queue_status = lambda key, **ctx: seen.append(key)

        gui._restore_status_after_paper_motion()

        self.assertEqual(seen, [reporting.STATUS_IDLE])


class GuiPaperModeChoiceTests(unittest.TestCase):
    def test_paper_mode_choices_are_empty_for_unsupported_family(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.device_from_profile("x6h")

        self.assertEqual(TiMiniPrintGUI._paper_mode_choices_for_device(device), ())

    def test_paper_mode_choices_are_exposed_for_luck_normal_a4(self) -> None:
        catalog = PrinterCatalog.load()
        base = catalog.device_from_profile("x6h")
        device = base.__class__(
            display_name=base.display_name,
            profile=base.profile,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant=base.protocol_variant,
            image_pipeline=get_protocol_definition(ProtocolFamily.LUCK_NORMAL_A4).behavior.default_image_pipeline,
            runtime_variant=base.runtime_variant,
            runtime_density_profile=base.runtime_density_profile,
            transport_target=base.transport_target,
            detection_rule_key=base.detection_rule_key,
            testing=base.testing,
            testing_note=base.testing_note,
        )

        labels = [label for label, _mode in TiMiniPrintGUI._paper_mode_choices_for_device(device)]
        self.assertEqual(labels, ["Plain roll", "Tag", "Black tag", "Folder", "Tattoo"])

    def test_paper_mode_choices_follow_qirui_variant_subset(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.detect_device("QIRUI_Q2_1234")

        self.assertIsNotNone(device)
        labels = [label for label, _mode in TiMiniPrintGUI._paper_mode_choices_for_device(device)]
        self.assertEqual(labels, ["Plain roll", "Tag"])


if __name__ == "__main__":
    unittest.main()
