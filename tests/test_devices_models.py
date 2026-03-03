from __future__ import annotations

import unittest

from tests.helpers import reset_registry_cache
from timiniprint.devices.models import (
    PrinterModel,
    PrinterModelAliasKind,
    PrinterModelAliasRegistry,
    PrinterModelHeadAlias,
    PrinterModelMacAlias,
    PrinterModelMatchSource,
    PrinterModelRegistry,
)


def _model(model_no: str, head_name: str) -> PrinterModel:
    return PrinterModel(
        model_no=model_no,
        model=1,
        size=1,
        paper_size=1,
        print_size=384,
        one_length=1,
        head_name=head_name,
        can_change_mtu=False,
        dev_dpi=203,
        img_print_speed=10,
        text_print_speed=8,
        img_mtu=180,
        new_compress=False,
        paper_num=1,
        interval_ms=4,
        thin_energy=0,
        moderation_energy=5000,
        deepen_energy=0,
        text_energy=8000,
        has_id=False,
        use_spp=False,
        new_format=False,
        can_print_label=False,
        label_value="",
        back_paper_num=0,
    )


class DevicesModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()

    def test_registry_load_get_and_detect(self) -> None:
        reg = PrinterModelRegistry.load()
        self.assertGreater(len(reg.models), 0)
        self.assertIsNotNone(reg.get("X6H"))
        match = reg.detect_with_origin("X6H-123")
        self.assertIsNotNone(match)

    def test_longest_prefix_priority(self) -> None:
        m1 = _model("A", "X")
        m2 = _model("B", "X6")
        reg = PrinterModelRegistry([m1, m2], PrinterModelAliasRegistry([], []))
        match = reg.detect_with_origin("X6H-AB")
        self.assertEqual(match.model.model_no, "B")
        self.assertEqual(match.source, PrinterModelMatchSource.HEAD_NAME)

    def test_alias_parse_validation_and_mac_alias(self) -> None:
        with self.assertRaises(ValueError):
            PrinterModelAliasRegistry._parse([{"bad": 1}])

        alias = PrinterModelMacAlias(suffixes=["59"], map_model_head_name="X6H")
        self.assertFalse(alias.matches("F4B3C8E3-C284-9C3A-C549-D786345CB553"))
        self.assertTrue(alias.matches("AA:BB:CC:DD:EE:59"))

    def test_alias_resolution_kind(self) -> None:
        reg = PrinterModelAliasRegistry(
            [PrinterModelHeadAlias(prefixes=["MX01"], map_model_head_name="X6H")],
            [PrinterModelMacAlias(suffixes=["59"], map_model_head_name="X7H")],
        )
        out = reg.resolve("MX01-AB", "AA:BB:CC:DD:EE:59")
        self.assertIsNotNone(out)
        self.assertEqual(out.kind, PrinterModelAliasKind.MAC)

    def test_phomemo_derived_aliases_map_to_new_base_models(self) -> None:
        reg = PrinterModelRegistry.load()

        tp84 = reg.detect_with_origin("TP84-ABCD")
        self.assertIsNotNone(tp84)
        self.assertEqual(tp84.model.model_no, "TP81")
        self.assertEqual(tp84.source, PrinterModelMatchSource.ALIAS)

        m836 = reg.detect_with_origin("M836-ABCD")
        self.assertIsNotNone(m836)
        self.assertEqual(m836.model.model_no, "M832")
        self.assertEqual(m836.source, PrinterModelMatchSource.ALIAS)

        q580 = reg.detect_with_origin("Q580-ABCD")
        self.assertIsNotNone(q580)
        self.assertEqual(q580.model.model_no, "Q302")
        self.assertEqual(q580.source, PrinterModelMatchSource.ALIAS)

        for derived in ("T02E-ABCD", "Q02E-ABCD", "C02E-ABCD"):
            mapped = reg.detect_with_origin(derived)
            self.assertIsNotNone(mapped)
            self.assertEqual(mapped.model.model_no, "T02")
            self.assertIn(
                mapped.source,
                (PrinterModelMatchSource.ALIAS, PrinterModelMatchSource.MODEL_NO),
            )


if __name__ == "__main__":
    unittest.main()
