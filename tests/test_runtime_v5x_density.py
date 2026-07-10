from __future__ import annotations

import unittest

from timiniprint.printing.runtime.v5x_density import (
    V5XJobContext,
    adjust_density_payload,
    start_delay_ms,
)


class V5XDensityPolicyTests(unittest.TestCase):
    def test_gray_density_keeps_head_specific_temperature_table(self) -> None:
        context = V5XJobContext(is_gray=True)

        self.assertEqual(
            [
                adjust_density_payload(
                    bytes([100]),
                    context,
                    temperature_c=temperature,
                    head_type="gaoya",
                )[0]
                for temperature in (70, 65, 60, 55, 50, 49)
            ],
            [56, 65, 75, 80, 85, 100],
        )
        self.assertEqual(
            [
                adjust_density_payload(
                    bytes([100]),
                    context,
                    temperature_c=temperature,
                    head_type="diya",
                )[0]
                for temperature in (70, 65, 60, 55, 50, 49)
            ],
            [56, 60, 65, 75, 80, 100],
        )

    def test_dot_density_keeps_coverage_and_temperature_bands(self) -> None:
        coverages = (0.4, 0.45, 0.6, 0.7)

        expected = {
            ("gaoya", 60): [100, 100, 100, 100],
            ("gaoya", 61): [48, 15, 15, 10],
            ("gaoya", 65): [36, 9, 5, 5],
            ("gaoya", 70): [22, 5, 3, 3],
            ("diya", 65): [60, 50, 50, 30],
            ("diya", 66): [50, 40, 40, 20],
            ("diya", 71): [40, 30, 30, 10],
        }
        for (head_type, temperature_c), values in expected.items():
            with self.subTest(head_type=head_type, temperature_c=temperature_c):
                actual = [
                    adjust_density_payload(
                        bytes([100]),
                        V5XJobContext(coverage_ratio=coverage),
                        temperature_c=temperature_c,
                        head_type=head_type,
                    )[0]
                    for coverage in coverages
                ]
                self.assertEqual(actual, values)

    def test_density_never_exceeds_user_value_and_ignores_other_payloads(self) -> None:
        context = V5XJobContext(coverage_ratio=0.8)

        self.assertEqual(
            adjust_density_payload(
                bytes([2]),
                context,
                temperature_c=80,
                head_type="gaoya",
            ),
            bytes([2]),
        )
        self.assertEqual(
            adjust_density_payload(
                b"\x01\x02",
                context,
                temperature_c=80,
                head_type="gaoya",
            ),
            b"\x01\x02",
        )

    def test_start_delay_keeps_high_coverage_and_density_rules(self) -> None:
        self.assertEqual(
            start_delay_ms(
                V5XJobContext(coverage_ratio=0.41),
                density_updated=False,
                head_type="gaoya",
            ),
            200,
        )
        self.assertEqual(
            start_delay_ms(
                V5XJobContext(coverage_ratio=0.4),
                density_updated=True,
                head_type="gaoya",
            ),
            60,
        )
        self.assertEqual(
            start_delay_ms(
                V5XJobContext(coverage_ratio=0.9),
                density_updated=False,
                head_type="diya",
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
