import json
import tempfile
import unittest
from pathlib import Path

from tripsense.core.data import load_city_data
from tripsense.core.intent import parse_local_intent
from tripsense.core.plan_ops import validate_plan_ops
from tripsense.core.planner import RoutePlanner
from tripsense.core.poi_supplement_store import pending_supplements_path
from tripsense.core.preference import PreferenceTracker, observation_from_mode
from tripsense.core.service import TripSenseService


class PlannerKalmanAndAlternativesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, distances = load_city_data("shanghai")
        cls.pois = pois
        cls.distances = distances

    def test_preference_shifts_candidate_scores(self):
        cold = PreferenceTracker()
        hot = PreferenceTracker()
        for _ in range(8):
            hot.update(observation_from_mode("deep", confidence=0.9))
        intent = parse_local_intent("想看历史文化", "shanghai")
        cold_plan = RoutePlanner(self.pois, self.distances, preference=cold)
        hot_plan = RoutePlanner(self.pois, self.distances, preference=hot)
        cold_candidates = cold_plan.build_candidates(intent, top_n=30)
        hot_candidates = hot_plan.build_candidates(intent, top_n=30)
        self.assertTrue(cold_candidates)
        self.assertTrue(hot_candidates)
        # Deep preference should not crash CCP planning.
        plan = hot_plan.plan(intent)
        self.assertLessEqual(plan.planned_minutes, plan.safe_budget_minutes + 1e-6)

    def test_plan_alternatives_return_distinct_profiles(self):
        planner = RoutePlanner(self.pois, self.distances)
        intent = parse_local_intent("今天四小时慢慢走", "shanghai")
        alternatives = planner.plan_alternatives(intent)
        self.assertGreaterEqual(len(alternatives), 1)
        profile_ids = [item[0] for item in alternatives]
        self.assertEqual(len(profile_ids), len(set(profile_ids)))

    def test_replan_locks_prefix_and_shortens_budget(self):
        planner = RoutePlanner(self.pois, self.distances)
        intent = parse_local_intent("今天想慢慢走四小时", "shanghai")
        current = planner.plan(intent)
        shorter = parse_local_intent("只剩三小时", "shanghai")
        shorter.available_minutes = 180
        ops = [
            {"op": "lock", "poi_ids": [current.stops[0].poi_id]},
            {"op": "replan", "scope": "tail"},
        ]
        updated = planner.replan(shorter, current, ops)
        self.assertEqual(updated.stops[0].poi_id, current.stops[0].poi_id)
        self.assertLessEqual(updated.available_minutes, 180)


class PlanOpsValidationTests(unittest.TestCase):
    def test_unknown_poi_and_ops_are_dropped(self):
        cleaned = validate_plan_ops(
            [
                {"op": "remove", "poi_id": "S1"},
                {"op": "remove", "poi_id": "NOPE"},
                {"op": "explode"},
                {"op": "avoid_outdoor", "enabled": True},
            ],
            allowed_poi_ids={"S1"},
            apply_realtime=False,
        )
        self.assertEqual(cleaned[0], {"op": "remove", "poi_id": "S1"})
        self.assertEqual(cleaned[1]["op"], "avoid_outdoor")
        self.assertFalse(cleaned[1]["enabled"])


class ServiceHybridTests(unittest.TestCase):
    def test_chat_without_llm_still_returns_plan_and_preference(self):
        service = TripSenseService()
        result = service.chat("今天想慢慢走看老建筑大概四小时", "shanghai")
        self.assertTrue(result["plan"]["stops"])
        self.assertIn("cultural_score", result["preference"]["state"])
        self.assertTrue(result["llm"]["intent_fallback"] or result["llm"]["provider"])

    def test_chat_with_current_plan_uses_local_ops_fallback(self):
        service = TripSenseService()
        first = service.chat("今天想慢慢走四小时", "shanghai")
        second = service.chat(
            "只剩三小时了",
            "shanghai",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertTrue(second["plan"]["stops"])
        self.assertLessEqual(second["intent"]["available_minutes"], 240)


class BeijingNamedPlaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, distances = load_city_data("beijing")
        cls.planner = RoutePlanner(pois, distances)

    def test_want_places_soft_lock_tiananmen_and_palace(self):
        intent = parse_local_intent(
            "我打算明天去北京玩一天，早上9点出发，晚上6点前结束。"
            "想去些经典景点，比如天安门、故宫，还有公园什么的。",
            "beijing",
        )
        self.assertEqual(intent.want_places, ["天安门", "故宫"])
        plan = self.planner.plan(intent)
        names = [stop.name for stop in plan.stops]
        self.assertTrue(any("天安门" in name for name in names), names)
        self.assertTrue(any("故宫" in name for name in names), names)

    def test_tiantan_resolves_to_main_park_not_daxing_shop(self):
        poi_id = self.planner._resolve_named_place("天坛", kind="place")
        self.assertIsNotNone(poi_id)
        row = self.planner._row_by_id(poi_id)
        self.assertIsNotNone(row)
        self.assertIn("天坛公园", str(row["name"]))
        self.assertNotIn("小艾", str(row["name"]))
        self.assertNotIn("研学", str(row["name"]))
        self.assertEqual(str(row["district"]), "东城区")
        self.assertNotEqual(poi_id, "B0LDR9I9ZU")

        shop = next(
            item
            for item in self.planner.pois.to_dict("records")
            if str(item.get("poi_id")) == "B0LDR9I9ZU"
        )
        park = next(
            item
            for item in self.planner.pois.to_dict("records")
            if str(item.get("poi_id")) == "ZTRIP_BJ_007"
        )
        self.assertGreater(
            self.planner._named_place_score("天坛", park, kind="place"),
            self.planner._named_place_score("天坛", shop, kind="place"),
        )
        self.assertIn("ZTRIP_BJ_007", self.planner.distances)

    def test_tiantan_children_use_parent_site_convention(self):
        frame = self.planner.pois
        main = frame[frame["poi_id"].astype(str) == "ZTRIP_BJ_007"].iloc[0]
        self.assertEqual(str(main["name"]), "天坛公园")
        self.assertEqual(str(main["parent_site"]), "天坛")
        self.assertTrue(bool(main["is_main_site"]))
        self.assertEqual(str(main["district"]), "东城区")
        self.assertEqual(str(main["poi_type_en"]), "park")

        children = frame[
            (frame["parent_site"].astype(str) == "天坛")
            & (frame["poi_id"].astype(str) != "ZTRIP_BJ_007")
        ]
        self.assertGreaterEqual(len(children), 15)
        for _, row in children.iterrows():
            self.assertEqual(str(row["parent_site"]), "天坛")
            self.assertFalse(bool(row["is_main_site"]))
        for fragment in ("昭亨门", "西天门", "祈年殿东配殿", "天坛公园-祈年殿"):
            hit = children[children["name"].astype(str) == fragment]
            self.assertFalse(hit.empty, fragment)
            self.assertEqual(str(hit.iloc[0]["parent_site"]), "天坛")
        # Unrelated 天坛-named shops stay untagged.
        shop = frame[frame["poi_id"].astype(str) == "B0LDR9I9ZU"].iloc[0]
        self.assertFalse(str(shop.get("parent_site") or "") in {"天坛", "天坛公园"})

    def test_unresolved_want_place_persists_search_coords(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            def mock_search(name, city, _intent):
                return {
                    "name": name,
                    "city": city,
                    "district": "海淀区",
                    "category": "park",
                    "category_name": "公园",
                    "gcj_lng": 116.3102,
                    "gcj_lat": 39.9928,
                    "source": "amap",
                    "reason": "mock amap hit",
                    "evidence": ["青云仙境园(模拟)"],
                }

            planner = RoutePlanner(
                self.planner.pois,
                self.planner.distances,
                place_search=mock_search,
                supplement_store_dir=data_dir,
            )
            intent = parse_local_intent("想去青云仙境园随便走走", "beijing")
            intent.want_places = ["青云仙境园"]
            ids = planner._resolve_want_place_ids(intent)
            self.assertTrue(ids)
            self.assertTrue(ids[0].startswith("ext:"))
            row = planner._row_by_id(ids[0])
            self.assertIsNotNone(row)
            self.assertEqual(row["city"], "北京")
            self.assertEqual(row["district"], "海淀区")
            self.assertAlmostEqual(float(row["gcj_lng"]), 116.3102, places=4)
            self.assertAlmostEqual(float(row["gcj_lat"]), 39.9928, places=4)
            self.assertTrue(row.get("is_synthetic"))
            pending = pending_supplements_path(data_dir)
            self.assertTrue(pending.exists())
            lines = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["name"], "青云仙境园")
            self.assertEqual(lines[0]["city"], "beijing")
            self.assertAlmostEqual(float(lines[0]["gcj_lng"]), 116.3102, places=4)
            self.assertEqual(lines[0]["source"], "amap")
            # Idempotent by name+city
            planner._supplement_named_place("青云仙境园", intent)
            lines2 = [line for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines2), 1)

    def test_unresolved_without_coords_soft_includes_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            def mock_search(name, city, _intent):
                return {
                    "name": name,
                    "city": city,
                    "district": "海淀区",
                    "category": "park",
                    "category_name": "公园",
                    "source": "amap",
                    "reason": "mock amap label only",
                    "evidence": ["青云仙境园(无坐标)"],
                }

            planner = RoutePlanner(
                self.planner.pois,
                self.planner.distances,
                place_search=mock_search,
                supplement_store_dir=data_dir,
            )
            intent = parse_local_intent("想去青云仙境园随便走走", "beijing")
            intent.want_places = ["青云仙境园"]
            ids = planner._resolve_want_place_ids(intent)
            self.assertTrue(ids)
            self.assertTrue(ids[0].startswith("ext:"))
            row = planner._row_by_id(ids[0])
            self.assertIsNotNone(row)
            self.assertEqual(row["district"], "海淀区")
            self.assertEqual(row.get("geo_quality"), "district")
            self.assertIsNotNone(row.get("gcj_lng"))
            self.assertIsNotNone(row.get("gcj_lat"))
            self.assertTrue(planner._approx_place_notes)
            self.assertIn("精确坐标", planner._approx_place_notes[0])
            pending = pending_supplements_path(data_dir)
            self.assertTrue(pending.exists())
            lines = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["name"], "青云仙境园")
            self.assertIsNone(lines[0]["gcj_lng"])
            self.assertIsNone(lines[0]["gcj_lat"])
            self.assertEqual(lines[0]["geo_quality"], "district")

    def test_unresolved_name_only_uses_city_or_sibling_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            planner = RoutePlanner(
                self.planner.pois,
                self.planner.distances,
                supplement_store_dir=data_dir,
            )
            intent = parse_local_intent("想去青云仙境园，也想去天安门", "beijing")
            intent.want_places = ["天安门", "青云仙境园"]
            ids = planner._resolve_want_place_ids(intent)
            self.assertGreaterEqual(len(ids), 2)
            soft_id = next(poi_id for poi_id in ids if str(poi_id).startswith("ext:"))
            row = planner._row_by_id(soft_id)
            self.assertIsNotNone(row)
            self.assertIn(row.get("geo_quality"), {"anchor", "city_prior", "district"})
            pending = pending_supplements_path(data_dir)
            self.assertTrue(pending.exists())
            lines = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(line.get("name") == "青云仙境园" for line in lines))
            soft_line = next(line for line in lines if line.get("name") == "青云仙境园")
            self.assertIsNone(soft_line.get("gcj_lng"))

    def test_coords_preferred_over_district_soft_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            def mock_search(name, city, _intent):
                return {
                    "name": name,
                    "city": city,
                    "district": "海淀区",
                    "category": "park",
                    "gcj_lng": 116.3102,
                    "gcj_lat": 39.9928,
                    "source": "amap",
                }

            planner = RoutePlanner(
                self.planner.pois,
                self.planner.distances,
                place_search=mock_search,
                supplement_store_dir=data_dir,
            )
            intent = parse_local_intent("想去青云仙境园随便走走", "beijing")
            intent.want_places = ["青云仙境园"]
            ids = planner._resolve_want_place_ids(intent)
            row = planner._row_by_id(ids[0])
            self.assertEqual(row.get("geo_quality"), "exact")
            self.assertAlmostEqual(float(row["gcj_lng"]), 116.3102, places=4)
            self.assertFalse(planner._approx_place_notes)

    def test_t2_swap_keeps_full_day_budget_and_includes_tiantan(self):
        service = TripSenseService()
        first = service.chat(
            "我打算明天去北京玩一天，早上9点出发，晚上6点前结束。"
            "想去些经典景点，比如天安门、故宫，还有公园什么的。",
            "beijing",
        )
        self.assertGreaterEqual(first["intent"]["available_minutes"], 420)
        second = service.chat(
            "能不能去掉故宫，换成更轻松的地方？比如像天坛这样的公园",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertGreaterEqual(second["intent"]["available_minutes"], 420)
        names = [stop["name"] for stop in second["plan"]["stops"]]
        self.assertTrue(any("天坛" in name for name in names), names)
        self.assertFalse(any("小艾" in name or "研学" in name for name in names), names)

        shorter = service.chat(
            "只剩三小时了",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertEqual(shorter["intent"]["available_minutes"], 180)


if __name__ == "__main__":
    unittest.main()
