from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.paper import (
    default_paper_preset_for_device,
    paper_presets_for_device,
    resolve_paper,
)
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PaperMode


class PrintingPaperPresetTests(unittest.TestCase):
    def test_plain_profile_resolves_explicit_default_preset(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")

        self.assertEqual([preset.key for preset in paper_presets_for_device(device)], ["default_384r"])

        paper = resolve_paper(device, PrintSettings())

        self.assertEqual(paper.key, "default_384r")
        self.assertEqual(paper.render_width_px, device.profile.default_paper_preset.render_width_px)
        self.assertIsNone(paper.paper_mode)

    def test_profile_paper_presets_are_exposed_directly(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")

        presets = paper_presets_for_device(device)
        default_preset = default_paper_preset_for_device(device)

        self.assertEqual(presets, device.profile.paper_presets)
        self.assertEqual([preset.key for preset in presets], ["tag_384r", "plain_384r"])
        self.assertIsNotNone(default_preset)
        assert default_preset is not None
        self.assertEqual(default_preset.key, "tag_384r")
        self.assertEqual(default_preset.paper_mode, PaperMode.TAG)

    def test_paper_preset_key_resolves_exact_catalog_key(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")

        paper = resolve_paper(device, PrintSettings(paper_preset_key="plain_384r"))

        self.assertEqual(paper.key, "plain_384r")
        self.assertEqual(paper.paper_mode, PaperMode.PLAIN)

    def test_profile_paper_preset_can_override_render_width(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        profile = replace(
            device.profile,
            paper_presets=(
                replace(
                    device.profile.default_paper_preset,
                    key="narrow",
                    label="Narrow roll",
                    render_width_px=128,
                ),
            ),
            default_paper_preset_key="narrow",
        )
        device = replace(device, profile=profile)

        paper = resolve_paper(device, PrintSettings())

        self.assertEqual(paper.key, "narrow")
        self.assertEqual(paper.render_width_px, 128)

    def test_resolve_paper_keeps_source_render_width_when_final_width_is_byte_aligned(self) -> None:
        device = PrinterCatalog.load().device_from_profile("15p3")

        paper = resolve_paper(device, PrintSettings())

        self.assertEqual(paper.render_width_px, 90)
        self.assertEqual(paper.paper_width_px, 96)

    def test_unknown_paper_preset_key_fails(self) -> None:
        device = PrinterCatalog.load().device_from_profile("luck_ppa2l")

        with self.assertRaisesRegex(
            ValueError,
            "(?s)does not support paper.*Available paper presets:.*tag_384r - Tag.*plain_384r - Plain roll",
        ):
            resolve_paper(device, PrintSettings(paper_preset_key="missing"))


if __name__ == "__main__":
    unittest.main()
