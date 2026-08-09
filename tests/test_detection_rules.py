from __future__ import annotations

import unittest

from timiniprint.devices.profiles import ModelDetection, WhitespaceMode


class ModelDetectionTests(unittest.TestCase):
    def test_mac_suffix_rule_does_not_match_uuid_address(self) -> None:
        detection = ModelDetection(
            prefixes=("MX05",),
            exact_names=(),
            mac_suffixes=("59",),
        )
        self.assertFalse(detection.matches("MX05-ABCD", "F4B3C8E3-C284-9C3A-C549-D786345CB553"))

    def test_mac_suffix_rule_matches_mac_address_suffix(self) -> None:
        detection = ModelDetection(
            prefixes=("MX05",),
            exact_names=(),
            mac_suffixes=("59",),
        )
        self.assertTrue(detection.matches("MX05-ABCD", "AA:BB:CC:DD:EE:59"))
        self.assertTrue(detection.matches("MX05-ABCD", "AA-BB-CC-DD-EE-59"))

    def test_exact_name_rule_matches_only_exact_name(self) -> None:
        detection = ModelDetection(
            prefixes=(),
            exact_names=("X6",),
        )
        self.assertTrue(detection.matches("X6", None))
        self.assertFalse(detection.matches("X6H-1234", None))

    def test_matched_specificity_prefers_exact_name_over_prefix(self) -> None:
        detection = ModelDetection(
            prefixes=("X6",),
            exact_names=("X6",),
        )

        self.assertEqual(
            detection.matched_specificity("X6", None),
            (2, 0, 2, 2, 1),
        )

    def test_matched_specificity_uses_the_same_case_folding_as_matches(self) -> None:
        detection = ModelDetection(prefixes=("WalkPrint-",))

        self.assertIsNone(detection.matched_specificity("walkprint-1234", None))
        self.assertEqual(
            detection.matched_specificity(
                "walkprint-1234",
                None,
                case_sensitive=False,
            ),
            (9, 0, 1, 10, 2),
        )

    def test_separator_suffix_prefix_does_not_create_base_alias(self) -> None:
        detection = ModelDetection(
            prefixes=("PPA2_",),
        )
        self.assertFalse(detection.matches("PPA2", None))
        self.assertTrue(detection.matches("PPA2_1234", None))

    def test_base_prefix_matches_source_style_suffixes(self) -> None:
        detection = ModelDetection(
            prefixes=("GT01",),
        )
        self.assertTrue(detection.matches("GT01", None))
        self.assertTrue(detection.matches("GT01-1234", None))
        self.assertTrue(detection.matches("GT01_1234", None))

    def test_substring_rule_matches_inside_normalized_name(self) -> None:
        detection = ModelDetection(substrings=("FDT4-0",))

        self.assertTrue(detection.matches("Printer FDT4-0 A", None))
        self.assertTrue(
            detection.matches(
                "printer fdt4-0 a",
                None,
                case_sensitive=False,
            )
        )
        self.assertFalse(detection.matches("FDT4-1", None))

    def test_all_of_requires_every_populated_name_trigger_group(self) -> None:
        detection = ModelDetection(
            prefixes=("S2",),
            substrings=("pro",),
            all_of=True,
        )

        self.assertTrue(detection.matches("S2-label-pro", None))
        self.assertFalse(detection.matches("S2-label", None))
        self.assertFalse(detection.matches("other-pro", None))
        self.assertFalse(detection.matches("S2-label-Pro", None))
        self.assertEqual(detection.names, ("S2",))

    def test_all_of_requires_at_least_two_trigger_groups(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least two"):
            ModelDetection(prefixes=("S2",), all_of=True)

    def test_matching_normalizes_private_copies_without_changing_public_spelling(self) -> None:
        detection = ModelDetection(
            exact_names=("PM 241",),
            prefixes=("BT 01-",),
        )

        self.assertEqual(detection.exact_names, ("PM 241",))
        self.assertEqual(detection.prefixes, ("BT 01-",))
        self.assertTrue(detection.matches("PM241", None))
        self.assertTrue(detection.matches("BT01-ABCD", None))

    def test_whitespace_modes_are_explicit_matching_policies(self) -> None:
        detection = ModelDetection(exact_names=("M50 ",))

        self.assertTrue(
            detection.matches(
                " M50 ",
                None,
                whitespace_mode=WhitespaceMode.TRIM,
            )
        )
        self.assertTrue(
            detection.matches(
                "M50 ",
                None,
                whitespace_mode=WhitespaceMode.PRESERVE,
            )
        )
        self.assertFalse(
            detection.matches(
                "M50",
                None,
                whitespace_mode=WhitespaceMode.PRESERVE,
            )
        )

    def test_public_names_include_all_sources_in_stable_order(self) -> None:
        detection = ModelDetection(
            marketing_names=("Retail name", "core"),
            exact_names=("BT 01", "retail NAME"),
            prefixes=("BT-", "PM_"),
            substrings=("Core-", "Tag_"),
        )

        self.assertEqual(
            detection.names,
            ("Retail name", "core", "BT 01", "BT", "PM", "Tag"),
        )

    def test_public_name_deduplication_keeps_whitespace_variants(self) -> None:
        detection = ModelDetection(exact_names=("PM241", "PM 241"))

        self.assertEqual(detection.names, ("PM241", "PM 241"))


if __name__ == "__main__":
    unittest.main()
