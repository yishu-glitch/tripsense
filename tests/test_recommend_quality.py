"""Product-level recommendation quality: the demo queries must not wander off theme."""

import unittest

from tripsense.core.data import load_city_data
from tripsense.core.dwell import estimate_dwell
from tripsense.core.intent import parse_local_intent
from tripsense.core.planner import RoutePlanner
from tripsense.core.scoring import rank_normalize, score_records


HERITAGE_ANCHORS = {"武康路", "思南路", "田子坊", "外滩", "外滩源", "安福路", "上生新所"}
SUBURBAN_MISTAKES = {"上海辰山植物园", "佘山国家旅游度假区", "滴水湖", "美兰湖景区"}
CORE_DISTRICTS = {"黄浦区", "徐汇区", "静安区", "长宁区", "虹口区"}


class ScoringUnitTests(unittest.TestCase):
    def test_rank_normalize_collapses_ties(self):
        ranks = rank_normalize([0.958, 0.958, 0.958, 0.99])
        self.assertAlmostEqual(ranks[0], ranks[1])
        self.assertLess(ranks[0], ranks[3])

    def test_false_heritage_is_incompatible_with_architecture_scene(self):
        intent = parse_local_intent("看老建筑，四小时", "shanghai")
        scored = score_records(
            [
                {
                    "poi_id": "good",
                    "name": "武康路",
                    "popularity": 0.958,
                    "poi_type_en": "heritage",
                    "primary_scene": "urban_walk",
                    "scene_tags": '["architecture", "history"]',
                    "district": "徐汇区",
                    "cultural_score": 0.8,
                    "nature_score": 0.1,
                    "commercial_score": 0.1,
                    "scenic_score": 0.6,
                    "indoor_score": 0.1,
                    "edu_score": 0.2,
                },
                {
                    "poi_id": "bad",
                    "name": "佘山国家旅游度假区",
                    "popularity": 0.958,
                    "poi_type_en": "heritage",
                    "primary_scene": "park_nature",
                    "scene_tags": '["nature", "outdoor"]',
                    "district": "松江区",
                    "cultural_score": 0.9,
                    "nature_score": 0.3,
                    "commercial_score": 0.1,
                    "scenic_score": 0.5,
                    "indoor_score": 0.1,
                    "edu_score": 0.2,
                },
            ],
            intent,
            [1.0, 0.2, 0.1, 0.5, 0.2, 0.4],
        )
        names = [row["name"] for row in scored]
        self.assertIn("武康路", names)
        self.assertNotIn("佘山国家旅游度假区", names)


class DwellModelTests(unittest.TestCase):
    def test_street_stays_are_shorter_than_museums(self):
        street = estimate_dwell(
            {"name": "武康路", "poi_type_en": "heritage", "primary_scene": "urban_walk"}
        )
        museum = estimate_dwell(
            {"name": "上海自然博物馆", "poi_type_en": "museum", "primary_scene": "museum_exhibition"}
        )
        self.assertLessEqual(street, 42.0)
        self.assertGreaterEqual(museum, 60.0)
        self.assertLess(street, museum)


class ShanghaiDemoQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, distances = load_city_data("shanghai")
        cls.planner = RoutePlanner(pois, distances)

    def test_old_buildings_stay_in_the_urban_heritage_cluster(self):
        plan = self.planner.plan(parse_local_intent("看老建筑，四小时", "shanghai"))
        names = [stop.name for stop in plan.stops]
        self.assertGreaterEqual(len(plan.stops), 3)
        self.assertTrue(set(names) & HERITAGE_ANCHORS, names)
        self.assertFalse(set(names) & SUBURBAN_MISTAKES, names)
        self.assertTrue({stop.district for stop in plan.stops} <= CORE_DISTRICTS | {"浦东新区"}, names)
        self.assertLessEqual(plan.planned_minutes, plan.safe_budget_minutes + 1e-6)
        self.assertTrue(plan.chance_constraint_satisfied)

    def test_family_half_day_keeps_museums_or_parks_and_enough_stops(self):
        plan = self.planner.plan(parse_local_intent("带孩子半天", "shanghai"))
        self.assertEqual(plan.scene, "亲子研学游")
        self.assertGreaterEqual(len(plan.stops), 2)
        self.assertFalse(set(stop.name for stop in plan.stops) & SUBURBAN_MISTAKES)
        types = {stop.poi_type for stop in plan.stops}
        self.assertTrue(types & {"museum", "park", "attraction", "culture"}, types)

    def test_evening_photo_skips_daytime_museums(self):
        plan = self.planner.plan(parse_local_intent("傍晚拍照三小时", "shanghai"))
        self.assertEqual(plan.mode, "photo")
        names = [stop.name for stop in plan.stops]
        self.assertFalse(any("博物馆" in name for name in names), names)
        self.assertGreaterEqual(len(plan.stops), 2)
        self.assertTrue(plan.stops[0].arrival_time)
        hour = int(plan.stops[0].arrival_time.split(":")[0])
        self.assertGreaterEqual(hour, 16)

    def test_start_from_the_bund_counts_outbound_travel(self):
        intent = parse_local_intent("从外滩出发看老建筑四小时", "shanghai")
        plan = self.planner.plan(intent)
        names = [stop.name for stop in plan.stops]
        self.assertFalse(set(names) & SUBURBAN_MISTAKES, names)
        if plan.stops:
            self.assertGreaterEqual(plan.stops[0].dwell_minutes, 25)

    def test_two_day_plan_splits_distinct_days(self):
        plan = self.planner.plan(parse_local_intent("两天看老建筑和街区", "shanghai"))
        self.assertEqual(plan.day_count, 2)
        day_one = [stop for stop in plan.stops if stop.day_index == 1]
        day_two = [stop for stop in plan.stops if stop.day_index == 2]
        self.assertTrue(day_one)
        self.assertTrue(day_two)
        self.assertFalse({stop.poi_id for stop in day_one} & {stop.poi_id for stop in day_two})
        # Geography can keep 武康路 off day 1; day 2 should still surface a
        # signature architecture street so the mental model is satisfied.
        day_two_names = {stop.name for stop in day_two}
        self.assertTrue(
            day_two_names & {"武康路", "安福路", "上生新所"}
            or {stop.name for stop in day_one} & {"武康路", "思南路", "田子坊"},
            (day_one, day_two),
        )


if __name__ == "__main__":
    unittest.main()
