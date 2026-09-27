import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tripsense.core.intent import parse_local_intent
from tripsense.core.service import TripSenseService
from tripsense.knowledge.contracts import RealtimePOIState
from tripsense.knowledge.realtime import StaticRealtimeProvider, UnavailableRealtimeProvider
from tripsense.knowledge.retrieval import LayeredKnowledgeRetriever
from tripsense.knowledge.stable import LexicalStableKnowledgeStore
from tripsense.knowledge.structured import StructuredPOIRepository, parse_opening_hours


def sample_frame() -> pd.DataFrame:
    base = {
        "city": "上海",
        "category_name": "历史街区",
        "poi_type_en": "heritage",
        "district": "徐汇区",
        "address": "测试地址",
        "gcj_lng": 121.45,
        "gcj_lat": 31.20,
        "rating": 4.7,
        "popularity": 0.95,
        "cultural_score": 0.9,
        "nature_score": 0.2,
        "commercial_score": 0.2,
        "scenic_score": 0.8,
        "indoor_score": 0.1,
        "edu_score": 0.5,
    }
    return pd.DataFrame(
        [
            {**base, "poi_id": "P1", "name": "武康路", "opentime": "全天"},
            {**base, "poi_id": "P2", "name": "普通商业装置", "opentime": "淮海路"},
        ]
    )


def sample_store(tmp_path: Path) -> LexicalStableKnowledgeStore:
    record = {
        "doc_id": "doc-1",
        "poi_id": "P1",
        "city": "shanghai",
        "name": "武康路",
        "aliases": [],
        "summary": "适合慢慢散步、观察老建筑和街区生活。",
        "experience_tags": ["慢节奏", "城市漫步"],
        "culture_tags": ["历史街区"],
        "architecture_tags": ["老建筑"],
        "suitable_for": ["建筑兴趣"],
        "avoid_if": [],
        "source": "test editorial",
        "source_version": "v1",
    }
    path = tmp_path / "knowledge.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return LexicalStableKnowledgeStore.from_jsonl(path)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture_dir = Path(__file__).with_name("_knowledge_fixture")
        self.fixture_dir.mkdir(exist_ok=True)

    def tearDown(self):
        for path in self.fixture_dir.glob("*"):
            path.unlink(missing_ok=True)
        self.fixture_dir.rmdir()

    def test_opening_hours_rejects_business_area_as_hours(self):
        windows, verified, flags = parse_opening_hours("淮海路")
        self.assertEqual(windows, [])
        self.assertFalse(verified)
        self.assertIn("unverified_opening_hours", flags)

    def test_opening_hours_accepts_amap_24_hour_value(self):
        windows, verified, flags = parse_opening_hours("24小时营业")
        self.assertEqual(windows, [("00:00", "23:59")])
        self.assertTrue(verified)
        self.assertEqual(flags, [])

    def test_structured_repository_respects_serving_route_flag(self):
        frame = sample_frame()
        frame["service_status"] = ["active", "active"]
        frame["is_route_candidate"] = [True, False]
        distances = {"P1": {}, "P2": {}}
        records = StructuredPOIRepository(frame, distances).filter(city="shanghai")
        self.assertEqual([item.poi_id for item in records], ["P1"])

    def test_stable_include_can_bypass_route_graph_flag(self):
        frame = sample_frame().iloc[:1].copy()
        frame["service_status"] = "active"
        frame["is_route_candidate"] = False
        records = StructuredPOIRepository(frame, {}).filter(city="shanghai", include_ids=["P1"])
        self.assertEqual([item.poi_id for item in records], ["P1"])

    def test_stable_retrieval_matches_experience_language(self):
        store = sample_store(self.fixture_dir)
        hits = store.search("今天想慢慢散步看看老建筑", city="shanghai")
        self.assertTrue(hits)
        self.assertEqual(hits[0].document.poi_id, "P1")

    def test_realtime_closed_candidate_is_only_reported_before_user_confirmation(self):
        frame = sample_frame()
        distances = {"P1": {"P2": {"travel_min": 10}}, "P2": {"P1": {"travel_min": 10}}}
        structured = StructuredPOIRepository(frame, distances)
        stable = sample_store(self.fixture_dir)
        provider = StaticRealtimeProvider(
            states={"P1": RealtimePOIState("P1", open_status="closed", message="临时关闭")}
        )
        retriever = LayeredKnowledgeRetriever(structured, stable, provider)
        intent = parse_local_intent("慢慢散步看看老建筑", city="shanghai")
        results = retriever.retrieve(intent)

        self.assertTrue(results)
        closed = next(item for item in results if item.poi.poi_id == "P1")
        self.assertEqual(closed.realtime_adjustment, 0.0)
        self.assertIsNone(closed.excluded_reason)
        self.assertEqual(closed.realtime_notices[0].category, "opening")
        self.assertTrue(closed.realtime_notices[0].requires_confirmation)

    def test_realtime_closed_candidate_can_be_excluded_after_user_confirmation(self):
        frame = sample_frame()
        distances = {"P1": {"P2": {"travel_min": 10}}, "P2": {"P1": {"travel_min": 10}}}
        structured = StructuredPOIRepository(frame, distances)
        stable = sample_store(self.fixture_dir)
        provider = StaticRealtimeProvider(
            states={"P1": RealtimePOIState("P1", open_status="closed", message="临时关闭")}
        )
        retriever = LayeredKnowledgeRetriever(structured, stable, provider)
        intent = parse_local_intent("慢慢散步看看老建筑", city="shanghai")
        intent.apply_realtime = True

        results = retriever.retrieve(intent)

        self.assertNotIn("P1", [item.poi.poi_id for item in results])

    def test_rain_is_reported_without_changing_candidate_scores(self):
        frame = sample_frame()
        distances = {"P1": {"P2": {"travel_min": 10}}, "P2": {"P1": {"travel_min": 10}}}
        structured = StructuredPOIRepository(frame, distances)
        stable = sample_store(self.fixture_dir)
        provider = StaticRealtimeProvider(weather="rain")
        retriever = LayeredKnowledgeRetriever(structured, stable, provider)
        intent = parse_local_intent("慢慢散步看看老建筑", city="shanghai")

        results = retriever.retrieve(intent)

        self.assertTrue(results)
        self.assertTrue(all(item.realtime_adjustment == 0.0 for item in results))
        self.assertTrue(
            any(
                notice.category == "weather" and notice.requires_confirmation
                for notice in results[0].realtime_notices
            )
        )

    def test_unavailable_realtime_is_explicit(self):
        snapshot = UnavailableRealtimeProvider().get_snapshot(
            "shanghai", ["P1"], at=datetime(2026, 9, 17, tzinfo=timezone.utc)
        )
        self.assertEqual(snapshot.availability, "unavailable")
        self.assertTrue(snapshot.warnings)

    def test_service_exposes_grounded_knowledge_candidates(self):
        result = TripSenseService().search_knowledge(
            "想慢慢走，看看老建筑和街区生活", "shanghai", limit=3
        )
        self.assertTrue(result["candidates"])
        first = result["candidates"][0]
        self.assertIn("evidence", first)
        self.assertIn("realtime_status", first)
        self.assertTrue(any(item["layer"] == "stable_rag" for item in first["evidence"]))

    def test_service_exposes_weather_notice_without_automatic_route_change(self):
        service = TripSenseService(realtime_provider=StaticRealtimeProvider(weather="rain"))

        plan = service.plan(
            "想慢慢走，看看老建筑和街区生活",
            "shanghai",
        )

        self.assertTrue(plan.realtime_notices)
        self.assertEqual(plan.realtime_notices[0]["category"], "weather")
        self.assertTrue(plan.realtime_notices[0]["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
