from __future__ import annotations

import unittest

from timiniprint.printing.runtime.v5g_density import (
    DensityLevels,
    mx06_continuous_plan,
    mx06_single_density_value,
    mx10_continuous_plan,
    mx10_continuous_series,
    mx10_single_density_value,
    pd01_continuous_plan,
    pd01_continuous_series,
    pd01_single_density_value,
    v5g_continuous_series,
)


class V5GRuntimeDensityPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.levels = DensityLevels(low=100, middle=130, high=150)

    def test_mx10_single_density_matches_source_thresholds(self) -> None:
        current = 180

        self.assertEqual(mx10_single_density_value(54, self.levels, current), 110)
        self.assertEqual(mx10_single_density_value(59, self.levels, current), 90)
        self.assertEqual(mx10_single_density_value(64, self.levels, current), 70)
        self.assertEqual(mx10_single_density_value(69, self.levels, current), 45)
        self.assertEqual(mx10_single_density_value(74, self.levels, current), 80)
        self.assertEqual(mx10_single_density_value(75, self.levels, current), current)

    def test_mx10_single_density_keeps_lower_user_values(self) -> None:
        self.assertEqual(mx10_single_density_value(54, self.levels, 120), 120)
        self.assertEqual(mx10_single_density_value(54, self.levels, 100), 100)
        self.assertEqual(mx10_single_density_value(59, self.levels, 95), 95)

    def test_pd01_single_density_matches_normal_source_branch(self) -> None:
        current = 180

        self.assertEqual(pd01_single_density_value(54, self.levels, current), 120)
        self.assertEqual(pd01_single_density_value(59, self.levels, current), 110)
        self.assertEqual(pd01_single_density_value(69, self.levels, current), 100)
        self.assertEqual(pd01_single_density_value(74, self.levels, current), 90)
        self.assertEqual(pd01_single_density_value(75, self.levels, current), 80)

    def test_mx06_single_density_uses_last_single_density(self) -> None:
        self.assertEqual(mx06_single_density_value(180, 0), 130)
        self.assertEqual(mx06_single_density_value(180, 150), 130)
        self.assertEqual(mx06_single_density_value(80, 200), 70)

    def test_mx10_continuous_plan_matches_source_thresholds(self) -> None:
        current = 180

        plan = mx10_continuous_plan(50, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (110, 1, 90, True))

        plan = mx10_continuous_plan(55, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (95, 1, 85, True))

        plan = mx10_continuous_plan(60, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (80, 1, 75, True))

        plan = mx10_continuous_plan(65, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (50, 1, 70, True))

        plan = mx10_continuous_plan(66, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (80, 1, 70, True))

    def test_mx10_continuous_plan_can_leave_first_packet_unchanged(self) -> None:
        plan = mx10_continuous_plan(50, self.levels, 100)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (100, 4, 95, False))

    def test_pd01_continuous_plan_matches_normal_source_branch(self) -> None:
        current = 180

        plan = pd01_continuous_plan(50, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (120, 1, 90, True))

        plan = pd01_continuous_plan(55, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (95, 1, 85, True))

        plan = pd01_continuous_plan(60, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (80, 1, 75, True))

        plan = pd01_continuous_plan(65, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (50, 1, 70, True))

        plan = pd01_continuous_plan(66, self.levels, current)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (80, 1, 70, True))

    def test_pd01_continuous_plan_matches_shallow_branch(self) -> None:
        current = 180

        plan = pd01_continuous_plan(50, self.levels, current, shallow=True)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (130, 1, 90, True))

        plan = pd01_continuous_plan(55, self.levels, current, shallow=True)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (120, 1, 85, True))

        plan = pd01_continuous_plan(60, self.levels, current, shallow=True)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (100, 1, 75, True))

        plan = pd01_continuous_plan(66, self.levels, current, shallow=True)
        self.assertEqual((plan.begin_density_value, plan.unchanged_packet_count, plan.minimum_density_value, plan.update_first_packet), (90, 1, 70, True))

    def test_mx06_continuous_plan_matches_recent_and_idle_branches(self) -> None:
        recent = mx06_continuous_plan(
            self.levels,
            180,
            last_record_density=150,
            recent_completion=True,
        )
        self.assertEqual(
            (
                recent.begin_density_value,
                recent.unchanged_packet_count,
                recent.minimum_density_value,
                recent.update_first_packet,
                recent.clamp_low_70,
            ),
            (120, 4, 95, True, True),
        )

        idle = mx06_continuous_plan(
            self.levels,
            180,
            last_record_density=150,
            recent_completion=False,
        )
        self.assertEqual(
            (
                idle.begin_density_value,
                idle.unchanged_packet_count,
                idle.minimum_density_value,
                idle.update_first_packet,
                idle.clamp_low_70,
            ),
            (110, 4, 95, True, False),
        )

    def test_mx10_continuous_series_matches_source_step_rules(self) -> None:
        self.assertEqual(mx10_continuous_series(140, 4, minimum_value=70), [125, 110, 95, 80])
        self.assertEqual(mx10_continuous_series(135, 3, minimum_value=70), [125, 115, 105])
        self.assertEqual(mx10_continuous_series(90, 4, minimum_value=70), [80, 70, 70, 70])

    def test_pd01_continuous_series_matches_normal_source_branch(self) -> None:
        self.assertEqual(pd01_continuous_series(130, 5), [115, 100, 85, 80, 75])
        self.assertEqual(pd01_continuous_series(95, 5), [80, 75, 70, 65, 60])
        self.assertEqual(pd01_continuous_series(60, 3), [55, 55, 55])

    def test_pd01_continuous_series_matches_shallow_branch(self) -> None:
        self.assertEqual(pd01_continuous_series(130, 5, shallow=True), [125, 120, 115, 110, 105])
        self.assertEqual(pd01_continuous_series(100, 3, shallow=True), [95, 95, 95])

    def test_generic_v5g_continuous_series_matches_mx06_style(self) -> None:
        self.assertEqual(v5g_continuous_series(140, 4), [130, 120, 110, 100])
        self.assertEqual(v5g_continuous_series(90, 5, clamp_low_70=True), [85, 80, 75, 70, 70])


if __name__ == "__main__":
    unittest.main()
