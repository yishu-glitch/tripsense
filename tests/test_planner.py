import unittest

import pandas as pd

from tripsense.core.intent import parse_local_intent
from tripsense.core.models import RoutePlan, Stop
from tripsense.core.planner import RoutePlanner


def sample_pois(city_prefix: str = "B") -> pd.DataFrame:
    rows = []
    types = ["heritage", "museum", "park", "attraction", "leisure"]
    for index in range(1, 16):
        poi_type = types[(index - 1) % len(types)]
        rows.append({
            "poi_id": f"{city_prefix}{index}",
            "name": f"景点{city_prefix}{index}",
            "category_name": poi_type,
            "poi_type_en": poi_type,
            "district": "测试区",
            "popularity": 1.0 - index * 0.01,
            "parent_site": None,
            "is_main_site": True,
            "cultural_score": 0.9 if poi_type in {"heritage", "museum"} else 0.2,
            "nature_score": 0.9 if poi_type == "park" else 0.2,
            "commercial_score": 0.9 if poi_type == "leisure" else 0.1,
            "scenic_score": 0.7,
            "indoor_score": 0.8 if poi_type == "museum" else 0.1,
            "edu_score": 0.8 if poi_type == "museum" else 0.2,
        })
    return pd.DataFrame(rows)


def sample_distances(prefix: str = "B") -> dict:
    result = {}
    for left in range(1, 16):
        result[f"{prefix}{left}"] = {}
        for right in range(1, 16):
            if left != right:
                result[f"{prefix}{left}"][f"{prefix}{right}"] = {"travel_min": 10.0}
    return result


class PlannerTests(unittest.TestCase):
    def test_scene_categories_are_enforced(self):
        planner = RoutePlanner(sample_pois(), sample_distances())
        intent = parse_local_intent("想深入看看历史和博物馆，大概四小时")
        candidates = planner.build_candidates(intent)
        self.assertTrue(candidates)
        self.assertTrue(all(row["poi_type_en"] in intent.categories for row in candidates))

    def test_city_uses_its_own_graph_not_beijing_bbox(self):
        planner = RoutePlanner(sample_pois("S"), sample_distances("S"))
        intent = parse_local_intent("第一次来，想看看经典景点，四小时", city="shanghai")
        candidates = planner.build_candidates(intent)
        self.assertGreater(len(candidates), 8)
        # Soft landmark seeds may appear as ext:* when absent from the toy catalog;
        # graph-backed rows must still be this city's S* ids, never Beijing B*.
        ids = [str(row["poi_id"]) for row in candidates]
        graph_ids = [poi_id for poi_id in ids if not poi_id.startswith("ext:")]
        self.assertTrue(graph_ids)
        self.assertTrue(all(poi_id.startswith("S") for poi_id in graph_ids), graph_ids)
        self.assertFalse(any(poi_id.startswith("B") for poi_id in ids), ids)

    def test_plan_respects_safe_budget(self):
        planner = RoutePlanner(sample_pois(), sample_distances())
        plan = planner.plan(parse_local_intent("历史文化，四小时"))
        self.assertLessEqual(plan.planned_minutes, plan.safe_budget_minutes)
        self.assertGreaterEqual(plan.satisfaction_probability, 0.899)

    def test_normalize_recalculates_adjacent_edges(self):
        distances = sample_distances()
        distances["B1"]["B3"] = {"travel_min": 27.0}
        planner = RoutePlanner(sample_pois(), distances)
        plan = RoutePlan(
            city="beijing",
            mode="balanced",
            scene="综合观光游",
            stops=[
                Stop("B1", "A", "x", "heritage", "", 60, 10, 1, ""),
                Stop("B3", "C", "x", "park", "", 60, None, 1, ""),
            ],
            available_minutes=240,
            safe_budget_minutes=201.6,
            planned_minutes=130,
            satisfaction_probability=0.99,
            reminder_level="comfortable",
            voice="",
        )
        normalized = planner.normalize(plan)
        self.assertEqual(normalized.stops[0].travel_to_next_minutes, 27.0)
        self.assertEqual(normalized.planned_minutes, 147.0)


if __name__ == "__main__":
    unittest.main()
