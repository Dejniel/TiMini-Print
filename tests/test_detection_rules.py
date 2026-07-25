from __future__ import annotations

import unittest

from timiniprint.devices.profiles import ModelDetection


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


if __name__ == "__main__":
    unittest.main()
