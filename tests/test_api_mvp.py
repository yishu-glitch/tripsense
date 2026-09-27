import importlib
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tripsense.core.journey import JourneyStore
from tripsense.core.models import RoutePlan, Stop
from tripsense.knowledge.realtime import AmapWeatherProvider


def sample_plan() -> RoutePlan:
    return RoutePlan(
        city="shanghai",
        mode="relaxed",
        scene="城市漫游",
        stops=[
            Stop(
                "S1",
                "武康路",
                "历史街区",
                "heritage",
                "徐汇区",
                65,
                None,
                1.0,
                "适合慢慢走",
            )
        ],
        available_minutes=180,
        safe_budget_minutes=150,
        planned_minutes=65,
        satisfaction_probability=0.98,
        reminder_level="spacious",
        voice="今天不用赶路。",
    )


class MvpApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = importlib.import_module("tripsense.api.app")
        self.previous_store = self.api.store
        self.previous_service = self.api.service
        self.temp_dir = Path(__file__).with_name("_api_mvp_test")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.api.store = JourneyStore(self.temp_dir / "tripsense.db")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.api.app, raise_app_exceptions=False),
            base_url="http://test",
        )
        self.journey_id = self.api.store.save(sample_plan())

    async def asyncTearDown(self):
        await self.client.aclose()
        self.api.store = self.previous_store
        self.api.service = self.previous_service
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_journey_record_and_diary_http_flow(self):
        journeys = await self.client.get("/api/v1/journeys")
        self.assertEqual(journeys.status_code, 200)
        self.assertEqual(journeys.json()["items"][0]["id"], self.journey_id)

        created = await self.client.post(
            f"/api/v1/journeys/{self.journey_id}/records",
            json={
                "day_index": 1,
                "stop_position": 1,
                "mood": "很松弛",
                "note": "树影很好看。",
                "photo_refs": ["local://wukang.jpg"],
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["place_name"], "武康路")

        records = await self.client.get(f"/api/v1/journeys/{self.journey_id}/records")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()["items"][0]["mood"], "很松弛")

        generated = await self.client.post(f"/api/v1/journeys/{self.journey_id}/diaries/generate")
        self.assertEqual(generated.status_code, 201)
        self.assertEqual(generated.json()["source_record_count"], 1)

        latest = await self.client.get(f"/api/v1/journeys/{self.journey_id}/diaries/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["id"], generated.json()["id"])

    async def test_root_serves_the_runnable_product_prototype(self):
        response = await self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('id="cold-start"', response.text)
        self.assertNotIn('src="tripsense-api.js', response.text)
        self.assertNotIn('src="tripsense-ui.js', response.text)
        self.assertIn("class TripSenseApi", response.text)
        self.assertIn("function countRecordedJourneys", response.text)

        client_script = await self.client.get("/tripsense-api.js")
        self.assertEqual(client_script.status_code, 200)
        self.assertIn("javascript", client_script.headers["content-type"])

        ui_script = await self.client.get("/tripsense-ui.js")
        self.assertEqual(ui_script.status_code, 200)
        self.assertIn("javascript", ui_script.headers["content-type"])

    async def test_health_reports_the_active_realtime_provider(self):
        response = await self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["realtime"],
            self.api.service.realtime_provider.provider_name,
        )
        self.assertEqual(response.json()["llm"]["provider"], self.api.service.llm_provider.provider_name)

    async def test_chat_endpoint_returns_message_intent_plan_and_provider_status(self):
        class FakeProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def interpret(self, _text, _baseline):
                from tripsense.llm import LlmInterpretation

                return LlmInterpretation(
                    mode="relaxed",
                    scene="城市漫游",
                    categories=["heritage", "park"],
                    assistant_reply="我先按慢节奏理解，再给你一版可以继续聊的路线。",
                )

            def propose_soft_prior(self, _text, _baseline):
                from tripsense.llm import SoftPlanningPrior

                return SoftPlanningPrior(
                    assistant_reply="我先按慢节奏理解，再给你一版可以继续聊的路线。",
                    intent_patch={
                        "mode": "relaxed",
                        "scene": "城市漫游",
                        "categories": ["heritage", "park"],
                    },
                )

            def choose_alternative(self, _text, _baseline, alternatives):
                from tripsense.llm import AlternativeChoice

                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="我先按慢节奏理解，再给你一版可以继续聊的路线。",
                )

            def propose_plan_ops(self, *_args, **_kwargs):
                from tripsense.llm import PlanOpsProposal

                return PlanOpsProposal(assistant_reply="继续调整。", ops=[])

            def write_reasons(self, _intent, evidence):
                from tripsense.llm import LlmReasonBundle

                stop_id = evidence["stops"][0]["poi_id"]
                return LlmReasonBundle(
                    route_reason="围绕街巷慢慢走，节奏留白。",
                    stop_reasons={stop_id: "适合慢走拍照"},
                )

            def rewrite_diary(self, _local):
                raise RuntimeError("unused")

        from tripsense.core.service import TripSenseService

        self.api.service = TripSenseService(llm_provider=FakeProvider())
        response = await self.client.post(
            "/api/v1/chat/respond",
            json={"city": "shanghai", "text": "我想慢慢逛街巷"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["message"],
            "我先按慢节奏理解，再给你一版可以继续聊的路线。",
        )
        self.assertEqual(response.json()["llm"]["provider"], "fake-llm")
        self.assertTrue(response.json()["plan"]["stops"])

    def test_api_service_factory_enables_amap_when_key_is_configured(self):
        builder = getattr(self.api, "build_service", None)
        self.assertIsNotNone(builder)

        with patch.dict(os.environ, {"AMAP_WEB_SERVICE_KEY": "secret-key"}, clear=False):
            configured = builder()

        self.assertIsInstance(configured.realtime_provider, AmapWeatherProvider)

    async def test_missing_journey_returns_404_instead_of_internal_error(self):
        response = await self.client.get("/api/v1/journeys/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "journey not found")

    async def test_diary_generation_without_records_returns_conflict(self):
        response = await self.client.post(f"/api/v1/journeys/{self.journey_id}/diaries/generate")

        self.assertEqual(response.status_code, 409)
        self.assertIn("record", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
