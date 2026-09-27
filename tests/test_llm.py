import json
import os
import shutil
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from tripsense.core.intent import parse_local_intent
from tripsense.core.journey import JourneyStore
from tripsense.core.models import RoutePlan, Stop
from tripsense.core.service import TripSenseService
from tripsense.llm import prompts as llm_prompts
from tripsense.llm import (
    AlternativeChoice,
    LlmDiaryDayRewrite,
    LlmDiaryRewrite,
    LlmInterpretation,
    LlmReasonBundle,
    OpenAICompatibleLlmProvider,
    PlanOpsProposal,
    SoftPlanningPrior,
    UnavailableLlmProvider,
    llm_provider_from_env,
)


class OpenAICompatibleLlmTests(unittest.TestCase):
    def test_interpret_uses_json_mode_and_returns_validated_fields(self):
        captured = {}

        def transport(url, headers, body, timeout):
            captured.update(url=url, headers=headers, body=body, timeout=timeout)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "mode": "relaxed",
                                    "scene": "城市漫游",
                                    "categories": ["heritage", "park", "invented"],
                                    "mood": "tired_or_relaxed",
                                    "pace": "slow",
                                    "available_minutes": 300,
                                    "assistant_reply": "我会保留街巷的松弛感，先给你一版可调整路线。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 18},
            }

        provider = OpenAICompatibleLlmProvider(
            api_key="secret",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=transport,
        )

        result = provider.interpret(
            "我有点累，想逛逛上海街巷",
            parse_local_intent("我有点累，想逛逛上海街巷", "shanghai"),
        )

        self.assertEqual(result.mode, "relaxed")
        self.assertEqual(result.categories, ["heritage", "park"])
        self.assertEqual(result.assistant_reply, "我会保留街巷的松弛感，先给你一版可调整路线。")
        self.assertEqual(captured["url"], "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertIn("JSON", captured["body"]["messages"][0]["content"])

    def test_soft_prior_keeps_want_places_and_asks_for_them_in_prompt(self):
        prior_prompt = llm_prompts.prompt_soft_prior()
        self.assertIn("want_places", prior_prompt)
        self.assertIn("用户点名的景点必须写入 want_places", prior_prompt)
        self.assertIn("可能不在本地库", prior_prompt)
        self.assertIn("联网检索", prior_prompt)
        self.assertIn("联网检索", llm_prompts.prompt_supplement_place())
        self.assertIn("真实坐标", llm_prompts.prompt_supplement_place())
        self.assertIn("不要编造假精确坐标", llm_prompts.prompt_supplement_place())
        self.assertNotIn("就近钉", llm_prompts.prompt_supplement_place())
        self.assertIn("不要输出排好序的行程清单或 poi_id", prior_prompt)

        def transport(*_args):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assistant_reply": "天安门和故宫先放进这一版，再配一点公园。",
                                    "intent_patch": {
                                        "mode": "relaxed",
                                        "scene": "综合观光游",
                                        "categories": ["attraction", "heritage", "park", "museum"],
                                        "want_places": ["天安门", "故宫", "公园"],
                                    },
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        provider = OpenAICompatibleLlmProvider(
            api_key="secret",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=transport,
        )
        prior = provider.propose_soft_prior(
            "想去天安门、故宫，还有公园",
            parse_local_intent("想去天安门、故宫，还有公园", "beijing"),
        )
        self.assertEqual(prior.intent_patch["want_places"], ["天安门", "故宫"])
        self.assertEqual(prior.intent_patch["scene"], "综合观光游")

    def test_write_reasons_keeps_only_known_stop_ids(self):
        def transport(*_args):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "route_reason": "围绕街巷串联老建筑，四小时节奏较松。",
                                    "stop_reasons": {
                                        "S1": "老建筑集中，适合慢走拍照",
                                        "UNKNOWN": "应被丢弃",
                                    },
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        provider = OpenAICompatibleLlmProvider(
            api_key="secret",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=transport,
        )
        intent = parse_local_intent("慢慢走", "shanghai")
        bundle = provider.write_reasons(
            intent,
            {"stops": [{"poi_id": "S1", "name": "武康路", "local_reason": "本地理由"}]},
        )
        self.assertIn("街巷", bundle.route_reason)
        self.assertEqual(bundle.stop_reasons, {"S1": "老建筑集中，适合慢走拍照"})

    def test_rewrite_diary_rejects_unknown_days_but_keeps_title(self):
        def transport(*_args):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "上海 · 慢下来的街巷日记",
                                    "subtitle": "把偶然也写进行程",
                                    "days": [
                                        {
                                            "day_index": 1,
                                            "title": "树影落在武康路",
                                            "narrative": "没有把路线走满，却把脚步留给了梧桐。",
                                        },
                                        {
                                            "day_index": 9,
                                            "title": "虚构的一天",
                                            "narrative": "不应出现",
                                        },
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        provider = OpenAICompatibleLlmProvider(
            api_key="secret",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=transport,
        )
        rewrite = provider.rewrite_diary(
            {
                "title": "旧标题",
                "subtitle": "旧副标题",
                "days": [{"day_index": 1, "title": "本地标题", "entries": []}],
            }
        )
        self.assertEqual(rewrite.title, "上海 · 慢下来的街巷日记")
        self.assertEqual(len(rewrite.days), 1)
        self.assertEqual(rewrite.days[0].day_index, 1)

    def test_invalid_or_empty_model_output_fails_closed(self):
        provider = OpenAICompatibleLlmProvider(
            api_key="secret",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=lambda *_args: {"choices": [{"message": {"content": ""}}]},
        )

        with self.assertRaises(ValueError):
            provider.interpret("想走走", parse_local_intent("想走走", "shanghai"))

    def test_factory_uses_environment_or_local_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(llm_provider_from_env(), UnavailableLlmProvider)

        configured_env = {
            "TRIPSENSE_LLM_PROVIDER": "openai-compatible",
            "TRIPSENSE_LLM_API_KEY": "secret",
            "TRIPSENSE_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "TRIPSENSE_LLM_MODEL": "deepseek-chat",
        }
        with patch.dict(os.environ, configured_env, clear=True):
            configured = llm_provider_from_env()

        self.assertIsInstance(configured, OpenAICompatibleLlmProvider)
        self.assertEqual(configured.model_name, "deepseek-chat")


class ServiceLlmTests(unittest.TestCase):
    def test_chat_uses_model_interpretation_and_reasons(self):
        class FakeProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def interpret(self, _text, _baseline):
                return LlmInterpretation(
                    mode="photo",
                    scene="城市漫游",
                    categories=["heritage", "attraction"],
                    assistant_reply="我理解你想看城市光影，先从步行友好的区域开始。",
                )

            def propose_soft_prior(self, _text, _baseline):
                return SoftPlanningPrior(
                    assistant_reply="我理解你想看城市光影，先从步行友好的区域开始。",
                    intent_patch={
                        "mode": "photo",
                        "scene": "城市漫游",
                        "categories": ["heritage", "attraction"],
                    },
                )

            def propose_plan_ops(self, *_args, **_kwargs):
                return PlanOpsProposal(assistant_reply="沿用当前路线微调。", ops=[])

            def choose_alternative(self, _text, _baseline, alternatives):
                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="我选更贴主题的一条。",
                )

            def write_reasons(self, _intent, evidence):
                stop_id = evidence["stops"][0]["poi_id"]
                return LlmReasonBundle(
                    route_reason="按光影节奏串联可步行街区。",
                    stop_reasons={stop_id: "光线与建筑同框，适合慢拍"},
                )

            def rewrite_diary(self, local_diary):
                raise RuntimeError("unused")

        result = TripSenseService(llm_provider=FakeProvider()).chat(
            "想拍城市光影",
            "shanghai",
            apply_realtime=False,
        )

        self.assertEqual(result["message"], "我选更贴主题的一条。")
        self.assertEqual(result["intent"]["mode"], "photo")
        self.assertFalse(result["intent"]["apply_realtime"])
        self.assertEqual(result["llm"]["provider"], "fake-llm")
        self.assertFalse(result["llm"]["fallback"])
        self.assertFalse(result["llm"]["reasons_fallback"])
        self.assertEqual(result["plan"]["route_reason"], "按光影节奏串联可步行街区。")
        self.assertEqual(result["plan"]["stops"][0]["reason"], "光线与建筑同框，适合慢拍")
        self.assertTrue(result["plan"]["stops"])
        self.assertIn("preference", result)

    def test_chat_passes_named_places_to_planner(self):
        class NamedPlaceProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def propose_soft_prior(self, _text, _baseline):
                return SoftPlanningPrior(
                    assistant_reply="天安门和故宫先放进这一版，再配一点能歇脚的公园。",
                    intent_patch={
                        "mode": "relaxed",
                        "scene": "综合观光游",
                        "categories": ["attraction", "heritage", "park", "museum"],
                        "pace": "slow",
                        "available_minutes": 480,
                        "want_places": ["天安门", "故宫"],
                    },
                )

            def choose_alternative(self, _text, _baseline, alternatives):
                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="先按你点的经典来。",
                )

            def propose_plan_ops(self, *_args, **_kwargs):
                return PlanOpsProposal(assistant_reply="沿用当前路线微调。", ops=[])

            def write_reasons(self, _intent, evidence):
                stop_id = evidence["stops"][0]["poi_id"]
                return LlmReasonBundle(
                    route_reason="先串你点名的经典，再留一点公园。",
                    stop_reasons={stop_id: "你点过的地方，先排进来"},
                )

            def interpret(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def rewrite_diary(self, *_args, **_kwargs):
                raise RuntimeError("unused")

        result = TripSenseService(llm_provider=NamedPlaceProvider()).chat(
            "明天去北京玩一天，想去天安门、故宫，还有公园",
            "beijing",
        )
        self.assertEqual(result["intent"]["want_places"], ["天安门", "故宫"])
        names = [stop["name"] for stop in result["plan"]["stops"]]
        self.assertTrue(any("天安门" in name for name in names), names)
        self.assertTrue(any("故宫" in name for name in names), names)

    def test_chat_falls_back_to_local_planner_when_model_fails(self):
        class FailingProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def interpret(self, _text, _baseline):
                raise TimeoutError("provider unavailable")

            def propose_soft_prior(self, *_args, **_kwargs):
                raise TimeoutError("provider unavailable")

            def propose_plan_ops(self, *_args, **_kwargs):
                raise TimeoutError("provider unavailable")

            def choose_alternative(self, *_args, **_kwargs):
                raise TimeoutError("provider unavailable")

            def write_reasons(self, _intent, _evidence):
                raise TimeoutError("provider unavailable")

            def rewrite_diary(self, _local):
                raise TimeoutError("provider unavailable")

        result = TripSenseService(llm_provider=FailingProvider()).chat(
            "今天想慢慢走四小时",
            "shanghai",
        )

        self.assertTrue(result["llm"]["fallback"])
        self.assertTrue(result["llm"]["reasons_fallback"])
        self.assertIn("provider unavailable", result["llm"]["error"])
        self.assertEqual(result["intent"]["mode"], "relaxed")
        self.assertTrue(result["message"])
        self.assertTrue(result["plan"]["stops"])
        self.assertIn("cultural_score", result["preference"]["state"])

    def test_provider_surfaces_http_auth_errors(self):
        def transport(*_args):
            raise HTTPError(
                "https://api.deepseek.com/v1/chat/completions",
                401,
                "Unauthorized",
                hdrs=None,
                fp=BytesIO(b'{"error":{"message":"Authentication Fails"}}'),
            )

        provider = OpenAICompatibleLlmProvider(
            api_key="bad",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            transport=transport,
        )
        with self.assertRaisesRegex(RuntimeError, "LLM HTTP 401"):
            provider.interpret("想走走", parse_local_intent("想走走", "shanghai"))

    def test_chat_replans_with_current_plan_context(self):
        class FakeProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def propose_soft_prior(self, *_args, **_kwargs):
                return SoftPlanningPrior(
                    assistant_reply="先给你一版四小时路线。",
                    intent_patch={"mode": "relaxed", "available_minutes": 240},
                )

            def choose_alternative(self, _text, _baseline, alternatives):
                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="先走这条。",
                )

            def propose_plan_ops(self, text, baseline, current_plan, candidates, **_kwargs):
                first = current_plan["stops"][0]["poi_id"]
                return PlanOpsProposal(
                    assistant_reply="时间紧了，我保留前半段并收紧后面。",
                    intent_patch={"available_minutes": 180},
                    ops=[
                        {"op": "lock", "poi_ids": [first]},
                        {"op": "replan", "scope": "tail"},
                    ],
                )

            def write_reasons(self, _intent, evidence):
                stop_id = evidence["stops"][0]["poi_id"]
                return LlmReasonBundle(
                    route_reason="在更紧的时间里保留你已认可的前半段。",
                    stop_reasons={stop_id: "先保留这一站作为锚点"},
                )

            def interpret(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def rewrite_diary(self, *_args, **_kwargs):
                raise RuntimeError("unused")

        service = TripSenseService(llm_provider=FakeProvider())
        first = service.chat("今天想慢慢走四小时", "shanghai")
        second = service.chat(
            "只剩三小时",
            "shanghai",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertEqual(second["intent"]["available_minutes"], 180)
        self.assertFalse(second["llm"]["ops_fallback"])
        self.assertEqual(second["plan"]["stops"][0]["poi_id"], first["plan"]["stops"][0]["poi_id"])

    def test_chat_place_swap_does_not_reset_inherited_day_budget(self):
        class SwapProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def propose_soft_prior(self, *_args, **_kwargs):
                return SoftPlanningPrior(
                    assistant_reply="先按一天慢慢看经典。",
                    intent_patch={"mode": "relaxed", "available_minutes": 480, "want_places": ["天安门", "故宫"]},
                )

            def choose_alternative(self, _text, _baseline, alternatives):
                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="先走这条。",
                )

            def propose_plan_ops(self, text, baseline, current_plan, candidates, **_kwargs):
                palace = next(
                    (
                        stop["poi_id"]
                        for stop in current_plan["stops"]
                        if "故宫" in str(stop.get("name", ""))
                    ),
                    current_plan["stops"][-1]["poi_id"],
                )
                keep = [stop["poi_id"] for stop in current_plan["stops"][:1]]
                return PlanOpsProposal(
                    assistant_reply="故宫先拿掉，天坛按公开信息补进这一版，细节以现场为准。",
                    intent_patch={"want_places": ["天坛"]},
                    ops=[
                        {"op": "remove", "poi_id": palace},
                        {"op": "lock", "poi_ids": keep},
                        {"op": "replan", "scope": "tail"},
                    ],
                )

            def write_reasons(self, _intent, evidence):
                stop_id = evidence["stops"][0]["poi_id"]
                return LlmReasonBundle(route_reason="换成能歇脚的公园。", stop_reasons={stop_id: "先留这一站"})

            def interpret(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def rewrite_diary(self, *_args, **_kwargs):
                raise RuntimeError("unused")

        service = TripSenseService(llm_provider=SwapProvider())
        first = service.chat(
            "我打算明天去北京玩一天，早上9点出发，晚上6点前结束。比如天安门、故宫，还有公园。",
            "beijing",
        )
        second = service.chat(
            "能不能去掉故宫，换成更轻松的地方？比如像天坛这样的公园",
            "beijing",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertGreaterEqual(second["intent"]["available_minutes"], 420)
        names = [stop["name"] for stop in second["plan"]["stops"]]
        self.assertTrue(any("天坛" in name for name in names), names)

    def test_chat_fixed_commitment_with_place_replans_near_anchor(self):
        class CommitmentProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def propose_soft_prior(self, *_args, **_kwargs):
                return SoftPlanningPrior(
                    assistant_reply="先按两天慢慢走整理一版。",
                    intent_patch={"mode": "relaxed", "days": 2, "available_minutes": 420},
                )

            def choose_alternative(self, _text, _baseline, alternatives):
                return AlternativeChoice(
                    chosen_profile_id=str(alternatives[0]["profile_id"]),
                    assistant_reply="先走这条。",
                )

            def propose_plan_ops(self, text, *_args, **_kwargs):
                if "演唱会" in text and "静安" not in text and "陆家嘴" not in text:
                    return PlanOpsProposal(
                        assistant_reply="第一天晚上你有安排。方便说下大概在哪个区域、几点开始吗？我好把下午收到附近。",
                        intent_patch={"busy_from_hour": 18.5},
                        ops=[],
                    )
                return PlanOpsProposal(
                    assistant_reply="好，我会把这天想逛的地方就近收到你赴约的那一带，前后留出缓冲。",
                    intent_patch={
                        "prefer_near": "静安" if "静安" in text else "陆家嘴",
                        "busy_from_hour": 18.5 if "晚上" in text else 12.0,
                        "busy_until_hour": 21.0 if "晚上" in text else 13.5,
                        "available_minutes": 300,
                    },
                    ops=[],
                )

            def write_reasons(self, _intent, evidence):
                return LlmReasonBundle(route_reason="就近赴约，少跨城赶。", stop_reasons={})

            def interpret(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def rewrite_diary(self, *_args, **_kwargs):
                raise RuntimeError("unused")

        service = TripSenseService(llm_provider=CommitmentProvider())
        first = service.chat("在上海和独自旅行，想用两天慢慢体验老建筑", "shanghai")
        self.assertEqual(first["plan"]["day_count"], 2)

        clarifying = service.chat(
            "我第一天晚上要看演唱会",
            "shanghai",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertEqual(clarifying["plan"]["stops"], first["plan"]["stops"])
        self.assertTrue(clarifying["needs_clarification"])
        self.assertFalse(clarifying["plan_updated"])
        self.assertEqual(clarifying["route_delta"]["kind"], "clarify")
        self.assertIn("区域", clarifying["message"])

        dinner = service.chat(
            "晚上和外地朋友吃饭在静安",
            "shanghai",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertEqual(dinner["intent"]["prefer_near"], "静安")
        self.assertTrue(dinner["llm"]["ops"])
        self.assertFalse(dinner["needs_clarification"])
        self.assertTrue(dinner["plan_updated"])
        self.assertEqual(dinner["plan"]["day_count"], 2)
        self.assertNotEqual(
            [s["poi_id"] for s in dinner["plan"]["stops"] if s["day_index"] == 1],
            [s["poi_id"] for s in first["plan"]["stops"] if s["day_index"] == 1],
        )
        self.assertNotIn("还能改", dinner["message"])
        self.assertNotIn("一定要留", dinner["message"])

        noon = service.chat(
            "中午要在陆家嘴开个会",
            "shanghai",
            current_plan=first["plan"],
            preference_state=first["preference"],
        )
        self.assertEqual(noon["intent"]["prefer_near"], "陆家嘴")
        self.assertTrue(noon["llm"]["ops"])


class DiaryLlmTests(unittest.TestCase):
    def test_generate_diary_uses_llm_rewrite_when_available(self):
        class FakeProvider:
            provider_name = "fake-llm"
            model_name = "fake-model"

            def interpret(self, *_args):
                raise RuntimeError("unused")

            def propose_soft_prior(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def propose_plan_ops(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def choose_alternative(self, *_args, **_kwargs):
                raise RuntimeError("unused")

            def write_reasons(self, *_args):
                raise RuntimeError("unused")

            def rewrite_diary(self, local_diary):
                day_index = local_diary["days"][0]["day_index"]
                return LlmDiaryRewrite(
                    title="上海 · 慢下来的街巷日记",
                    subtitle="把偶然也写进行程",
                    days=[
                        LlmDiaryDayRewrite(
                            day_index=day_index,
                            title="树影落在武康路",
                            narrative="没有把路线走满，却把脚步留给了梧桐。",
                        )
                    ],
                )

        temp_dir = Path(__file__).with_name("_diary_llm_test")
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            store = JourneyStore(temp_dir / "tripsense.db")
            journey_id = store.save(
                RoutePlan(
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
                    available_minutes=240,
                    safe_budget_minutes=200,
                    planned_minutes=65,
                    satisfaction_probability=0.9,
                    reminder_level="spacious",
                    voice="今天不用赶路。",
                )
            )
            store.add_record(
                journey_id,
                day_index=1,
                stop_position=1,
                mood="舒服",
                note="树影很好看。",
            )

            diary = store.generate_diary(journey_id, llm_provider=FakeProvider())

            self.assertEqual(diary["title"], "上海 · 慢下来的街巷日记")
            self.assertEqual(diary["days"][0]["title"], "树影落在武康路")
            self.assertIn("梧桐", diary["days"][0]["narrative"])
            self.assertEqual(diary["generated_by"], "llm:fake-llm/fake-model")
            self.assertEqual(diary["days"][0]["entries"][0]["note"], "树影很好看。")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
