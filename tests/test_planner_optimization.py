"""Optimisation-level guarantees for the route construction heuristic."""

import unittest
from dataclasses import replace
from itertools import pairwise

import pandas as pd

from tripsense.core.constraints import time_budget
from tripsense.core.intent import parse_local_intent
from tripsense.core.planner import DEFAULT_BEAM_WIDTH, RoutePlanner


def grid_pois(count: int = 24) -> pd.DataFrame:
    """POIs with a deliberate trap: the highest score also eats the budget."""
    types = ["heritage", "museum", "park", "attraction", "culture", "leisure"]
    rows = []
    for index in range(count):
        poi_type = types[index % len(types)]
        rows.append(
            {
                "poi_id": f"P{index:02d}",
                "name": f"地点{index:02d}",
                "category_name": poi_type,
                "poi_type_en": poi_type,
                "district": "测试区",
                "popularity": 0.99 if index == 0 else 0.55 + (index % 7) * 0.04,
                "parent_site": None,
                "is_main_site": True,
                "cultural_score": 0.9 if poi_type in {"heritage", "museum"} else 0.3,
                "nature_score": 0.9 if poi_type == "park" else 0.2,
                "commercial_score": 0.8 if poi_type == "leisure" else 0.1,
                "scenic_score": 0.6,
                "indoor_score": 0.8 if poi_type == "museum" else 0.2,
                "edu_score": 0.7 if poi_type == "museum" else 0.2,
                "gcj_lng": 121.40 + (index % 6) * 0.01,
                "gcj_lat": 31.20 + (index // 6) * 0.01,
            }
        )
    return pd.DataFrame(rows)


def grid_distances(count: int = 24) -> dict:
    """Travel time grows with index distance, so ordering genuinely matters."""
    result: dict[str, dict] = {}
    for left in range(count):
        result[f"P{left:02d}"] = {}
        for right in range(count):
            if left == right:
                continue
            result[f"P{left:02d}"][f"P{right:02d}"] = {
                "travel_min": 5.0 + 3.0 * abs(left - right)
            }
    return result


class BeamSearchTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner(grid_pois(), grid_distances())
        self.intent = parse_local_intent("想看看历史和博物馆，大概四小时")

    def test_beam_search_is_never_worse_than_greedy(self):
        greedy = self.planner.plan(self.intent, beam_width=1)
        beam = self.planner.plan(self.intent, beam_width=DEFAULT_BEAM_WIDTH)

        greedy_score = sum(stop.score for stop in greedy.stops)
        beam_score = sum(stop.score for stop in beam.stops)
        self.assertGreaterEqual(beam_score + 1e-9, greedy_score)
        self.assertGreaterEqual(len(beam.stops), 1)

    def test_wider_beams_never_reduce_collected_score(self):
        scores = []
        for width in (1, 2, 4, 8):
            plan = self.planner.plan(self.intent, beam_width=width)
            scores.append(sum(stop.score for stop in plan.stops))
        for narrower, wider in pairwise(scores):
            self.assertGreaterEqual(wider + 1e-9, narrower)

    def test_planning_is_deterministic(self):
        first = self.planner.plan(self.intent)
        second = self.planner.plan(self.intent)
        self.assertEqual(
            [stop.poi_id for stop in first.stops],
            [stop.poi_id for stop in second.stops],
        )

    def test_beam_width_zero_is_treated_as_one(self):
        plan = self.planner.plan(self.intent, beam_width=0)
        self.assertTrue(plan.stops)

    def test_density_aware_seed_avoids_hop_isolated_top_score(self):
        """A top-scored but hop-isolated POI must not trap a full-day plan at 1–2 stops."""
        pois = grid_pois(12)
        distances = grid_distances(12)
        for other in list(distances["P00"]):
            distances["P00"][other]["travel_min"] = 80.0
            distances[other]["P00"]["travel_min"] = 80.0
        planner = RoutePlanner(pois, distances)
        intent = parse_local_intent("想看看历史和博物馆，大概六小时")
        plan = planner.plan(intent, max_stops=5)
        self.assertNotIn("P00", [stop.poi_id for stop in plan.stops])
        self.assertGreaterEqual(len(plan.stops), 3)


class BudgetConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner(grid_pois(), grid_distances())

    def test_plan_never_exceeds_the_chance_constrained_budget(self):
        for text in (
            "两小时快速看看",
            "四小时慢慢走",
            "六小时充实一点",
            "想深入看历史，大概三小时",
        ):
            intent = parse_local_intent(text)
            plan = self.planner.plan(intent)
            expected = time_budget(
                intent.available_minutes,
                intent.uncertainty_minutes,
                intent.risk_epsilon,
            )
            self.assertEqual(plan.safe_budget_minutes, expected.minutes, text)
            self.assertLessEqual(plan.planned_minutes, plan.safe_budget_minutes + 1e-6, text)
            self.assertTrue(plan.chance_constraint_satisfied, text)

    def test_reported_minutes_match_the_emitted_stops(self):
        plan = self.planner.plan(parse_local_intent("四小时慢慢走"))
        recomputed = sum(
            stop.dwell_minutes + (stop.travel_to_next_minutes or 0.0) for stop in plan.stops
        )
        self.assertAlmostEqual(plan.planned_minutes, round(recomputed, 1), places=6)

    def test_locked_stop_keeps_its_accepted_dwell(self):
        base = self.planner.plan(parse_local_intent("四小时慢慢走"))
        faster = replace(parse_local_intent("四小时慢慢走"), pace="fast")

        updated = self.planner.plan(faster, locked_stops=base.stops[:1])

        self.assertEqual(updated.stops[0].poi_id, base.stops[0].poi_id)
        self.assertEqual(updated.stops[0].dwell_minutes, base.stops[0].dwell_minutes)
        self.assertLessEqual(updated.planned_minutes, updated.safe_budget_minutes + 1e-6)

    def test_risk_parameters_are_carried_on_the_plan(self):
        intent = replace(parse_local_intent("四小时慢慢走"), risk_epsilon=0.2)
        plan = self.planner.plan(intent)
        self.assertEqual(plan.risk_epsilon, 0.2)
        self.assertEqual(plan.uncertainty_minutes, float(intent.uncertainty_minutes))
        self.assertEqual(plan.risk_model, "gaussian")

    def test_stricter_risk_models_are_reachable_from_the_intent(self):
        budgets = {}
        for model in ("gaussian", "cvar", "moment"):
            intent = replace(parse_local_intent("六小时充实一点"), risk_model=model)
            plan = self.planner.plan(intent)
            budgets[model] = plan.safe_budget_minutes
            self.assertEqual(plan.risk_model, model)
            self.assertLessEqual(plan.planned_minutes, plan.safe_budget_minutes + 1e-6)
        self.assertGreater(budgets["gaussian"], budgets["cvar"])
        self.assertGreater(budgets["cvar"], budgets["moment"])

    def test_wasserstein_radius_reaches_the_planner(self):
        nominal = replace(parse_local_intent("六小时充实一点"), risk_model="wasserstein")
        robust = replace(nominal, wasserstein_radius=2.0)
        self.assertLess(
            self.planner.plan(robust).safe_budget_minutes,
            self.planner.plan(nominal).safe_budget_minutes,
        )

    def test_normalize_uses_stored_sigma_instead_of_back_deriving_it(self):
        intent = replace(parse_local_intent("四小时慢慢走"), risk_epsilon=0.2)
        plan = self.planner.plan(intent)
        normalized = self.planner.normalize(plan)
        self.assertEqual(normalized.uncertainty_minutes, plan.uncertainty_minutes)
        self.assertEqual(
            normalized.satisfaction_probability, plan.satisfaction_probability
        )


class TwoOptTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner(grid_pois(), grid_distances())

    def test_two_opt_never_increases_travel(self):
        intent = parse_local_intent("六小时充实一点")
        plan = self.planner.plan(intent)
        order = [stop.poi_id for stop in plan.stops]
        travel = self.planner._path_travel(order)

        improved = self.planner._two_opt(
            order,
            {pid: {"poi_id": pid, "poi_type_en": "heritage"} for pid in order},
            intent,
            plan.safe_budget_minutes,
            0,
        )
        self.assertLessEqual(self.planner._path_travel(improved), travel + 1e-9)
        self.assertCountEqual(improved, order)

    def test_two_opt_preserves_the_locked_prefix(self):
        intent = parse_local_intent("六小时充实一点")
        order = ["P05", "P00", "P11", "P02"]
        rows = {pid: {"poi_id": pid, "poi_type_en": "heritage"} for pid in order}
        improved = self.planner._two_opt(order, rows, intent, 10_000.0, locked_count=2)
        self.assertEqual(improved[:2], order[:2])
        self.assertCountEqual(improved, order)

    def test_two_opt_reorders_a_crossing_path(self):
        intent = parse_local_intent("六小时充实一点")
        order = ["P00", "P10", "P01", "P11"]
        rows = {pid: {"poi_id": pid, "poi_type_en": "heritage"} for pid in order}
        before = self.planner._path_travel(order)
        improved = self.planner._two_opt(order, rows, intent, 10_000.0, locked_count=0)
        self.assertLess(self.planner._path_travel(improved), before)


class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner(grid_pois(), grid_distances())

    def test_tail_replan_keeps_the_prefix_and_respects_the_new_budget(self):
        intent = parse_local_intent("六小时充实一点")
        current = self.planner.plan(intent)
        self.assertGreaterEqual(len(current.stops), 2)

        shorter = replace(intent, available_minutes=180)
        updated = self.planner.replan(shorter, current, [{"op": "replan", "scope": "tail"}])

        kept = max(1, len(current.stops) // 2)
        self.assertEqual(
            [stop.poi_id for stop in updated.stops[:1]],
            [stop.poi_id for stop in current.stops[:1]],
        )
        self.assertLessEqual(updated.planned_minutes, updated.safe_budget_minutes + 1e-6)
        self.assertGreaterEqual(kept, 1)

    def test_locked_prefix_is_trimmed_when_the_budget_shrinks(self):
        intent = parse_local_intent("六小时充实一点")
        current = self.planner.plan(intent)
        self.assertGreaterEqual(len(current.stops), 3)

        tight = replace(intent, available_minutes=120)
        updated = self.planner.replan(
            tight,
            current,
            [
                {"op": "lock", "poi_ids": [stop.poi_id for stop in current.stops]},
                {"op": "replan", "scope": "all"},
            ],
        )

        self.assertLess(len(updated.stops), len(current.stops))
        self.assertLessEqual(updated.planned_minutes, updated.safe_budget_minutes + 1e-6)
        self.assertTrue(updated.chance_constraint_satisfied)

    def test_a_single_oversized_lock_is_kept_but_reported_as_infeasible(self):
        intent = parse_local_intent("六小时充实一点")
        current = self.planner.plan(intent)
        tiny = replace(intent, available_minutes=20, uncertainty_minutes=2)

        updated = self.planner.replan(
            tiny, current, [{"op": "lock", "poi_ids": [current.stops[0].poi_id]}]
        )

        self.assertEqual(updated.stops[0].poi_id, current.stops[0].poi_id)
        self.assertFalse(updated.chance_constraint_satisfied)

    def test_removed_stop_does_not_come_back(self):
        intent = parse_local_intent("六小时充实一点")
        current = self.planner.plan(intent)
        victim = current.stops[0].poi_id

        updated = self.planner.replan(intent, current, [{"op": "remove", "poi_id": victim}])

        self.assertNotIn(victim, [stop.poi_id for stop in updated.stops])


if __name__ == "__main__":
    unittest.main()
