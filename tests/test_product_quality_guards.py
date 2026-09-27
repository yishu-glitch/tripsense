"""Transferable product-quality guards (cue/tier/collapse/clarify — not case ids)."""

from __future__ import annotations

import unittest

from tripsense.core.data import load_city_data
from tripsense.core.intent import (
    is_descriptive_placeholder,
    parse_local_intent,
    soft_seed_signature_places,
    utterance_adds_indoor,
    utterance_is_artistic_style_shift,
    utterance_is_vague_stop_replace,
    utterance_switches_to_indoor,
    utterance_wants_hutong,
    utterance_wants_landmarks,
)
from tripsense.core.models import Intent, RoutePlan, Stop
from tripsense.core.planner import RoutePlanner
from tripsense.core.scoring import core_landmark_bonus, score_records
from tripsense.core.service import TripSenseService
from tripsense.core.preference import PreferenceTracker
from tripsense.llm import UnavailableLlmProvider


class LandmarkSeedTests(unittest.TestCase):
    def test_landmark_cues_seed_city_signatures(self):
        bj = parse_local_intent(
            "下午到北京出差，半天，快速打卡几个标志性的地方",
            "beijing",
        )
        self.assertTrue(utterance_wants_landmarks(bj.utterance))
        self.assertTrue(any("天安门" in p or "故宫" in p for p in bj.want_places), bj.want_places)

        sh = parse_local_intent("上海玩一天，想打卡几个经典地方", "shanghai")
        self.assertTrue(any("外滩" in p for p in sh.want_places), sh.want_places)

    def test_heritage_day_seeds_signature_not_only_obscure(self):
        intent = parse_local_intent("一天多打卡古建筑", "beijing")
        self.assertIn("故宫", intent.want_places)
        self.assertTrue(
            any(name in intent.want_places for name in ("天坛公园", "颐和园", "恭王府")),
            intent.want_places,
        )

    def test_user_named_places_are_not_overwritten(self):
        intent = parse_local_intent(
            "想去些经典景点，比如天安门、故宫，还有公园",
            "beijing",
        )
        self.assertEqual(intent.want_places, ["天安门", "故宫"])

    def test_placeholder_cafe_names_are_rejected(self):
        self.assertTrue(is_descriptive_placeholder("有北京特色的咖啡馆"))
        self.assertTrue(is_descriptive_placeholder("互动体验展馆"))
        self.assertFalse(is_descriptive_placeholder("故宫"))
        intent = parse_local_intent(
            "累了，只去一家有北京特色的咖啡馆",
            "beijing",
        )
        self.assertFalse(
            any(is_descriptive_placeholder(p) for p in intent.want_places),
            intent.want_places,
        )


class ParentSiteCollapseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, distances = load_city_data("beijing")
        cls.planner = RoutePlanner(pois, distances)

    def test_name_hyphen_children_count_as_parent_child(self):
        self.assertTrue(
            self.planner._names_are_parent_child("天坛公园", "天坛公园-祈年殿")
        )

    def test_dedupe_keeps_one_per_parent_site(self):
        frame = self.planner.pois
        main = frame[frame["poi_id"].astype(str) == "ZTRIP_BJ_007"].iloc[0].to_dict()
        children = frame[
            (frame["parent_site"].astype(str) == "天坛")
            & (frame["poi_id"].astype(str) != "ZTRIP_BJ_007")
        ].head(3)
        records = [main, *[row.to_dict() for _, row in children.iterrows()]]
        for row in records:
            row["_score"] = float(row.get("popularity") or 0)
        collapsed = self.planner._deduplicate_parent_site_records(records)
        parents = {
            self.planner._parent_site_key(row)
            for row in collapsed
            if self.planner._parent_site_key(row)
        }
        self.assertEqual(len(parents), 1)
        self.assertEqual(len(collapsed), 1)
        self.assertTrue(
            str(collapsed[0].get("is_main_site")).lower() in {"true", "1"}
        )
        self.assertIn("天坛公园", str(collapsed[0].get("name")))

    def test_emit_collapses_tiantan_and_qinian(self):
        intent = parse_local_intent("带孩子亲子研学约六小时", "beijing")
        # Force both parent children into a synthetic selection collapse.
        main = self.planner._row_by_id("ZTRIP_BJ_007")
        children = self.planner.pois[
            (self.planner.pois["parent_site"].astype(str) == "天坛")
            & (self.planner.pois["poi_id"].astype(str) != "ZTRIP_BJ_007")
        ].head(2)
        selected = [dict(main), *[row.to_dict() for _, row in children.iterrows()]]
        collapsed = self.planner._collapse_parent_site_stops(selected)
        names = [str(row["name"]) for row in collapsed]
        self.assertEqual(len(names), 1)
        self.assertIn("天坛公园", names[0])
        self.assertFalse(any("祈年殿" in name for name in names))


class ScoringLandmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, _ = load_city_data("beijing")
        cls.rows = pois.to_dict("records")

    def test_landmark_intent_prefers_core_over_nightlife(self):
        intent = parse_local_intent(
            "北京半天，快速打卡几个标志性地方",
            "beijing",
        )
        square = next(r for r in self.rows if str(r.get("name")) == "天安门广场")
        barish = {
            "name": "后海酒吧街",
            "attraction_tier": "",
            "is_main_site": False,
            "parent_site": "",
            "popularity": 0.95,
            "district": "西城区",
            "poi_type_en": "leisure",
            "primary_scene": "indoor_leisure",
        }
        self.assertGreater(
            core_landmark_bonus(square, intent),
            core_landmark_bonus(barish, intent),
        )
        self.assertLess(core_landmark_bonus(barish, intent), 0)


class VagueReplaceClarifyTests(unittest.TestCase):
    def test_vague_replace_detector(self):
        self.assertTrue(
            utterance_is_vague_stop_replace("孩子没兴趣，换成更互动的展馆")
        )
        self.assertFalse(utterance_is_vague_stop_replace("前门换成798"))
        self.assertFalse(utterance_is_vague_stop_replace("去掉故宫，换成天坛"))

    def test_phase_c_asks_which_stop(self):
        service = TripSenseService(llm_provider=UnavailableLlmProvider())
        first = service.chat("带孩子亲子研学约六小时，北京", "beijing")
        self.assertTrue(first["plan"]["stops"])
        second = service.chat(
            "孩子没兴趣，换成更互动的展馆",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertTrue(second["needs_clarification"])
        self.assertFalse(second["plan_updated"])
        self.assertEqual(
            [s["poi_id"] for s in second["plan"]["stops"]],
            [s["poi_id"] for s in first["plan"]["stops"]],
        )
        self.assertIn("哪一个", second["message"])
        self.assertFalse(
            any("互动体验" in s["name"] for s in second["plan"]["stops"])
        )


class AddIndoorKeepsOutdoorTests(unittest.TestCase):
    def test_add_indoor_detector(self):
        self.assertTrue(utterance_adds_indoor("再加室内，打卡多一些"))
        self.assertTrue(utterance_adds_indoor("太热，加个室内凉快的"))
        self.assertFalse(utterance_adds_indoor("天气太热，换成室内"))

    def test_switch_indoor_detector(self):
        self.assertTrue(utterance_switches_to_indoor("太热换成室内"))
        self.assertTrue(
            utterance_switches_to_indoor(
                "感觉都是室外，明天下午特别热，能不能换成室内的景点？"
            )
        )
        self.assertFalse(utterance_switches_to_indoor("再加室内，打卡多一些"))
        self.assertFalse(utterance_is_vague_stop_replace("太热换成室内"))

    def test_local_ops_lock_parks_when_adding_indoor(self):
        stops = [
            Stop(
                poi_id="p1",
                name="天坛公园",
                category="公园",
                poi_type="park",
                district="东城区",
                dwell_minutes=60,
                travel_to_next_minutes=15,
                score=0.8,
                reason="",
            ),
            Stop(
                poi_id="p2",
                name="国家自然博物馆",
                category="博物馆",
                poi_type="museum",
                district="东城区",
                dwell_minutes=60,
                travel_to_next_minutes=None,
                score=0.7,
                reason="",
            ),
        ]
        plan = RoutePlan(
            city="beijing",
            mode="balanced",
            scene="亲子研学游",
            stops=stops,
            available_minutes=480,
            safe_budget_minutes=420,
            planned_minutes=200,
            satisfaction_probability=0.9,
            reminder_level="comfortable",
            voice="",
        )
        ops = TripSenseService._local_dynamic_ops("再加室内，打卡多一些", plan)
        lock = next(op for op in ops if op["op"] == "lock")
        self.assertIn("p1", lock["poi_ids"])
        prefer = next(op for op in ops if op["op"] == "prefer_categories")
        self.assertIn("park", prefer["categories"])
        self.assertIn("museum", prefer["categories"])

    def test_switch_indoor_replans_with_indoor_stop(self):
        service = TripSenseService(llm_provider=UnavailableLlmProvider())
        first = service.chat(
            "明天下午北京出差，半天，快速打卡标志性地方",
            "beijing",
        )
        self.assertTrue(first["plan"]["stops"])
        outdoorish = first["plan"]
        second = service.chat(
            "感觉都是室外，明天下午特别热，能不能换成室内的景点？",
            "beijing",
            current_plan=outdoorish,
            preference_state=first["preference"],
        )
        self.assertTrue(second["plan_updated"], second.get("message"))
        self.assertFalse(second["needs_clarification"])
        names = " ".join(s["name"] for s in second["plan"]["stops"])
        indoor_tokens = ("国博", "博物馆", "博物院", "美术馆", "室内")
        self.assertTrue(
            any(token in names for token in indoor_tokens)
            or any(
                s.get("poi_type") == "museum" or "博物" in s["name"]
                for s in second["plan"]["stops"]
            ),
            [s["name"] for s in second["plan"]["stops"]],
        )


class HutongAddKeepsParkTests(unittest.TestCase):
    def test_hutong_detector_and_seed(self):
        self.assertTrue(
            utterance_wants_hutong(
                "更想多看点胡同里的老建筑或者民国时期的建筑，走路就行"
            )
        )
        intent = parse_local_intent(
            "路线不错，但我更想多看点有特色的历史建筑，比如胡同里的那种老建筑或者民国时期的建筑。走路就行。",
            "beijing",
        )
        self.assertTrue(
            any(
                name in intent.want_places
                for name in ("南锣鼓巷", "烟袋斜街", "五道营", "恭王府")
            ),
            intent.want_places,
        )
        self.assertFalse(
            any(is_descriptive_placeholder(p) for p in intent.want_places),
            intent.want_places,
        )

    def test_hutong_add_keeps_summer_palace(self):
        service = TripSenseService(llm_provider=UnavailableLlmProvider())
        first = service.chat(
            "我想一天内逛尽可能多的北京经典景点，特别喜欢古建筑，多打卡几个地方",
            "beijing",
        )
        names_t1 = [s["name"] for s in first["plan"]["stops"]]
        self.assertTrue(any("颐和园" in n for n in names_t1), names_t1)
        second = service.chat(
            "路线不错，但我更想多看点有特色的历史建筑，比如胡同里的那种老建筑或者民国时期的建筑。另外我体力好，可以多走点路，不用打车，走路就行。",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        names_t2 = [s["name"] for s in second["plan"]["stops"]]
        self.assertTrue(any("颐和园" in n for n in names_t2), names_t2)
        blob = " ".join(names_t2)
        self.assertTrue(
            any(token in blob for token in ("南锣", "烟袋", "五道营", "恭王府", "胡同")),
            names_t2,
        )


class ArtisticStyleShiftTests(unittest.TestCase):
    def test_artistic_not_vague_clarify(self):
        self.assertTrue(
            utterance_is_artistic_style_shift(
                "798我之前去过了，有没有更文艺一点的地方？最好人少一点，拍照好看。"
            )
        )
        self.assertFalse(
            utterance_is_vague_stop_replace(
                "798我之前去过了，有没有更文艺一点的地方？最好人少一点，拍照好看。"
            )
        )
        # Still clarify for vague interactive swap without style neighborhood cue.
        self.assertTrue(utterance_is_vague_stop_replace("孩子没兴趣，换成更互动的展馆"))

    def test_artistic_style_updates_route(self):
        service = TripSenseService(llm_provider=UnavailableLlmProvider())
        first = service.chat(
            "我想在北京玩一天，喜欢拍照打卡，节奏轻松点",
            "beijing",
        )
        second = service.chat(
            "798我之前去过了，有没有更文艺一点的地方？最好人少一点，拍照好看。",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertFalse(second["needs_clarification"], second.get("message"))
        self.assertNotIn("哪一个", second["message"])
        self.assertTrue(second["plan_updated"] or second["intent"].get("scene") == "城市漫游")
        wants = second["intent"].get("want_places") or []
        self.assertTrue(
            any(name in wants for name in ("五道营", "烟袋斜街", "南锣鼓巷", "芳草地"))
            or any(
                token in " ".join(s["name"] for s in second["plan"]["stops"])
                for token in ("五道营", "烟袋", "南锣", "芳草", "琉璃")
            ),
            (wants, [s["name"] for s in second["plan"]["stops"]]),
        )


class CafeNearPreferTests(unittest.TestCase):
    def test_cafe_near_sets_prefer_near_not_placeholder(self):
        intent = parse_local_intent(
            "累了，五道营附近咖啡馆坐坐，不去圆明园",
            "beijing",
        )
        self.assertEqual(intent.prefer_near, "五道营")
        self.assertFalse(
            any(is_descriptive_placeholder(p) for p in intent.want_places)
        )

    def test_cafe_linger_with_named_areas(self):
        intent = parse_local_intent(
            "五道营和芳草地听起来不错！不过我想在咖啡馆多待会儿",
            "beijing",
        )
        self.assertEqual(intent.prefer_near, "五道营")
        self.assertNotIn("咖啡馆", intent.want_places)


class OpenerRouteSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pois, distances = load_city_data("beijing")
        cls.planner = RoutePlanner(pois, distances)

    def test_landmark_half_day_is_classic_cluster(self):
        intent = parse_local_intent(
            "明天下午北京出差，半天，快速打卡标志性地方",
            "beijing",
        )
        plan = self.planner.plan(intent)
        names = [s.name for s in plan.stops]
        blob = " ".join(names)
        self.assertTrue(
            any(token in blob for token in ("天安门", "故宫", "国博", "中山公园", "天坛")),
            names,
        )
        self.assertFalse(any("酒吧" in name for name in names), names)

    def test_photo_day_not_single_parent_cluster(self):
        intent = parse_local_intent("北京拍一天，人少、光线好", "beijing")
        plan = self.planner.plan(intent)
        parents = {
            self.planner._parent_site_key(
                {"name": s.name, "parent_site": getattr(s, "parent_site", "")}
            )
            or s.name
            for s in plan.stops
        }
        # At least avoid many children of one site; preferably multi-area names.
        self.assertGreaterEqual(len(plan.stops), 2)
        tiantan_children = sum(1 for s in plan.stops if "天坛" in s.name)
        self.assertLessEqual(tiantan_children, 1, [s.name for s in plan.stops])

    def test_heritage_day_includes_famous_site(self):
        intent = parse_local_intent("一天多打卡古建筑", "beijing")
        plan = self.planner.plan(intent)
        names = " ".join(s.name for s in plan.stops)
        self.assertTrue(
            any(token in names for token in ("故宫", "天坛", "颐和园", "恭王府", "天安门")),
            [s.name for s in plan.stops],
        )


if __name__ == "__main__":
    unittest.main()
