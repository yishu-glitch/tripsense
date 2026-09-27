from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from tripsense.knowledge.realtime import RealtimeProvider, UnavailableRealtimeProvider
from tripsense.knowledge.retrieval import LayeredKnowledgeRetriever
from tripsense.knowledge.stable import LexicalStableKnowledgeStore
from tripsense.knowledge.structured import StructuredPOIRepository
from tripsense.llm import LlmProvider, LlmReasonBundle, UnavailableLlmProvider

from .data import load_city_data, stable_knowledge_path
from .intent import (
    inherit_session_intent,
    is_descriptive_placeholder,
    parse_denied_places,
    parse_local_intent,
    sanitize_session_patch,
    soft_seed_signature_places,
    utterance_adds_indoor,
    utterance_is_artistic_style_shift,
    utterance_is_vague_stop_replace,
    utterance_switches_to_indoor,
    utterance_wants_hutong,
)
from .models import Intent, RoutePlan
from .plan_ops import (
    SoftPlanningPrior,
    apply_intent_patch,
    route_plan_from_dict,
)
from .planner import RoutePlanner
from .preference import ATTR_COLUMNS, ATTR_DIMS, PreferenceObservation, PreferenceTracker


class TripSenseService:
    """Application service: Kalman preferences + CCP planner + optional LLM B/C/A."""

    def __init__(
        self,
        data_dir: Path | None = None,
        realtime_provider: RealtimeProvider | None = None,
        llm_provider: LlmProvider | None = None,
        preference: PreferenceTracker | None = None,
    ):
        self.data_dir = data_dir
        self.realtime_provider = realtime_provider or UnavailableRealtimeProvider()
        self.llm_provider = llm_provider or UnavailableLlmProvider()
        self.preference = preference or PreferenceTracker()
        self._planners: dict[str, RoutePlanner] = {}

    def planner(self, city: str) -> RoutePlanner:
        if city not in self._planners:
            pois, distances = load_city_data(city, self.data_dir)
            knowledge_path = stable_knowledge_path(city, self.data_dir)
            if knowledge_path.exists():
                stable = LexicalStableKnowledgeStore.from_jsonl(knowledge_path)
            else:
                stable = LexicalStableKnowledgeStore([])
            structured = StructuredPOIRepository(pois, distances)
            retriever = LayeredKnowledgeRetriever(
                structured=structured,
                stable=stable,
                realtime=self.realtime_provider,
            )
            self._planners[city] = RoutePlanner(
                pois,
                distances,
                knowledge=retriever,
                preference=self.preference,
                place_supplement=self._supplement_named_place,
                place_search=self._search_named_place,
                supplement_store_dir=self.data_dir,
            )
        else:
            self._planners[city].preference = self.preference
        return self._planners[city]

    def plan(
        self,
        text: str,
        city: str = "beijing",
        *,
        apply_realtime: bool = False,
    ) -> RoutePlan:
        intent = parse_local_intent(text, city, apply_realtime=apply_realtime)
        self.preference.observe_intent(intent.mode, intent.pace)
        plan = self.planner(city).plan(intent)
        plan, _meta = self._apply_llm_reasons(intent, plan)
        return plan

    def chat(
        self,
        text: str,
        city: str = "shanghai",
        *,
        apply_realtime: bool = False,
        current_plan: dict[str, Any] | None = None,
        preference_state: dict[str, Any] | None = None,
        previous_intent: dict[str, Any] | None = None,
    ) -> dict:
        if preference_state:
            self.preference = PreferenceTracker.from_dict(preference_state)

        baseline = parse_local_intent(text, city, apply_realtime=apply_realtime)
        errors: list[str] = []
        intent_fallback = False
        ops_fallback = False
        alternatives_fallback = False
        reasons_fallback = False
        message = ""
        preference_weights: dict[str, float] = {}
        selected_profile: str | None = None
        alternatives_payload: list[dict[str, Any]] = []
        used_ops: list[dict[str, Any]] = []
        current = None
        asked_to_clarify = False

        if current_plan:
            # Phase C: incremental replan from current route.
            try:
                current = route_plan_from_dict(current_plan)
            except (TypeError, ValueError) as exc:
                return self._chat_first_round(
                    text,
                    city,
                    baseline,
                    apply_realtime=apply_realtime,
                    bootstrap_error=str(exc)[:240],
                )

            prior_intent = previous_intent or (
                current_plan.get("intent") if isinstance(current_plan.get("intent"), dict) else None
            )
            baseline = inherit_session_intent(
                baseline, text, current=current, previous=prior_intent
            )
            intent = baseline
            try:
                candidates = [
                    {
                        "poi_id": str(row["poi_id"]),
                        "name": str(row.get("name", "")),
                        "category": str(row.get("category_name", row.get("poi_type_en", ""))),
                    }
                    for row in self.planner(city).build_candidates(baseline)[:12]
                ]
                proposal = self.llm_provider.propose_plan_ops(
                    text,
                    baseline,
                    current.to_dict(),
                    candidates,
                    apply_realtime=apply_realtime,
                )
                intent = apply_intent_patch(
                    baseline, sanitize_session_patch(proposal.intent_patch, text)
                )
                message = proposal.assistant_reply
                used_ops = proposal.ops
                asked_to_clarify = bool(getattr(proposal, "needs_clarification", False))
            except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                ops_fallback = True
                intent_fallback = True
                errors.append(str(exc)[:240] or type(exc).__name__)
                intent = self._local_dynamic_intent(baseline, text, current)
                message = self._local_dynamic_message(text, current)
                used_ops = self._local_dynamic_ops(text, current)

            intent = self._preserve_plan_days(intent, current)
            intent = soft_seed_signature_places(intent)
            # Strip category placeholders the model may have stuffed into want_places.
            if intent.want_places:
                cleaned_wants = [
                    name
                    for name in intent.want_places
                    if not is_descriptive_placeholder(name)
                ]
                if cleaned_wants != list(intent.want_places):
                    intent = replace(intent, want_places=cleaned_wants)
            intent = soft_seed_signature_places(intent)

            vague_replace = utterance_is_vague_stop_replace(text)
            if vague_replace and current.stops:
                asked_to_clarify = True
                used_ops = []
                should_replan = False
                stop_names = "、".join(stop.name for stop in current.stops[:5])
                message = (
                    f"可以换成更对味的一站。你更想换掉现在这几站里的哪一个？"
                    f"（{stop_names}）说一下站名就行，我再动那一站。"
                )
            else:
                intent, used_ops = self._apply_scenario_ops(text, current, intent, used_ops)
                # Incomplete fixed-slot commitments: ask, keep the previous route.
                incomplete_commitment = self._incomplete_fixed_commitment(intent)
                essentials_ready = bool((intent.prefer_near or "").strip()) and (
                    intent.busy_from_hour is not None
                )
                if incomplete_commitment or (asked_to_clarify and not essentials_ready):
                    used_ops = []
                    should_replan = False
                    asked_to_clarify = True
                else:
                    should_replan = bool(used_ops) or self._soft_constraints_actionable(
                        baseline, intent
                    )
                if should_replan and not used_ops:
                    day_one = [s for s in current.stops if s.day_index == 1] or list(
                        current.stops
                    )
                    keep = (
                        day_one[: max(1, min(2, len(day_one) // 2 or 1))] if day_one else []
                    )
                    used_ops = []
                    if keep:
                        used_ops.append(
                            {"op": "lock", "poi_ids": [stop.poi_id for stop in keep]}
                        )
                    used_ops.append({"op": "replan", "scope": "tail"})

            self.preference.observe_intent(intent.mode, intent.pace)
            try:
                if should_replan:
                    plan = self.planner(city).replan(
                        intent,
                        current,
                        used_ops,
                        preference_weights=preference_weights or None,
                    )
                else:
                    plan = current
            except ValueError as exc:
                errors.append(str(exc)[:240])
                plan = current
                ops_fallback = True
        else:
            # Phase B (+ optional A): soft prior then alternatives.
            prior = SoftPlanningPrior(assistant_reply="")
            try:
                prior = self.llm_provider.propose_soft_prior(text, baseline)
                intent = apply_intent_patch(baseline, prior.intent_patch)
                preference_weights = dict(prior.preference_weights)
                message = prior.assistant_reply
            except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                intent_fallback = True
                errors.append(str(exc)[:240] or type(exc).__name__)
                intent = baseline
                message = "我先按你的描述整理一版路线；之后想放慢、换地方，或天气有变化，都可以继续说。"

            intent = soft_seed_signature_places(intent)
            if intent.want_places:
                cleaned_wants = [
                    name
                    for name in intent.want_places
                    if not is_descriptive_placeholder(name)
                ]
                if cleaned_wants != list(intent.want_places):
                    intent = replace(intent, want_places=cleaned_wants)

            self.preference.observe_intent(intent.mode, intent.pace)
            if preference_weights:
                # Treat explicit LLM weights as a soft, attribute-only observation.
                # Pace is deliberately left unobserved so a wording change cannot
                # silently reset the tracked pace.
                import numpy as np

                self.preference.update(
                    PreferenceObservation(
                        kind="llm_weights",
                        values=np.array(
                            [
                                float(
                                    np.clip(
                                        preference_weights.get(
                                            name, self.preference.state[i]
                                        ),
                                        0.0,
                                        1.0,
                                    )
                                )
                                for i, name in enumerate(ATTR_COLUMNS)
                            ],
                            dtype=float,
                        ),
                        dims=ATTR_DIMS,
                        confidence=0.5,
                        source="soft_prior",
                    )
                )

            planner = self.planner(city)
            alternatives = planner.plan_alternatives(
                intent,
                preference_weights=preference_weights or None,
            )
            alternatives_payload = [
                {
                    "profile_id": profile_id,
                    "summary": summary,
                    "stop_names": [stop.name for stop in alt.stops],
                    "planned_minutes": alt.planned_minutes,
                    "plan": alt.to_dict(),
                }
                for profile_id, alt, summary in alternatives
            ]
            plan = alternatives[0][1]
            selected_profile = alternatives[0][0]
            if len(alternatives) >= 2:
                try:
                    choice = self.llm_provider.choose_alternative(
                        text,
                        intent,
                        [
                            {
                                "profile_id": item["profile_id"],
                                "summary": item["summary"],
                                "stop_names": item["stop_names"],
                                "planned_minutes": item["planned_minutes"],
                            }
                            for item in alternatives_payload
                        ],
                    )
                    selected_profile = choice.chosen_profile_id
                    message = choice.assistant_reply or message
                    for profile_id, alt, _summary in alternatives:
                        if profile_id == selected_profile:
                            plan = alt
                            break
                except (
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                    TypeError,
                    ValueError,
                ) as exc:
                    alternatives_fallback = True
                    errors.append(str(exc)[:240] or type(exc).__name__)

        plan, reasons_meta = self._apply_llm_reasons(intent, plan)
        reasons_fallback = bool(reasons_meta.get("fallback"))
        if reasons_meta.get("error"):
            errors.append(str(reasons_meta["error"]))

        llm_meta: dict[str, Any] = {
            "provider": self.llm_provider.provider_name,
            "model": self.llm_provider.model_name,
            "fallback": intent_fallback or ops_fallback,
            "intent_fallback": intent_fallback,
            "ops_fallback": ops_fallback,
            "alternatives_fallback": alternatives_fallback,
            "reasons_fallback": reasons_fallback,
            "selected_profile": selected_profile,
            "ops": used_ops,
        }
        if errors:
            llm_meta["error"] = " | ".join(errors)[:240]
        previous_fp = (
            tuple((stop.day_index, stop.poi_id) for stop in current.stops)
            if current is not None
            else None
        )
        current_fp = tuple((stop.day_index, stop.poi_id) for stop in plan.stops)
        plan_updated = previous_fp is None or previous_fp != current_fp
        incomplete = self._incomplete_fixed_commitment(intent)
        needs_clarification = bool(
            incomplete or (asked_to_clarify and not (intent.prefer_near or "").strip())
        )
        if current is not None and plan_updated:
            # A real replan is not a clarifying turn.
            needs_clarification = False
        plan_payload = plan.to_dict()
        plan_payload["intent"] = intent.to_dict()
        missing_notes = list(getattr(self.planner(city), "_missing_place_notes", []) or [])
        approx_notes = list(getattr(self.planner(city), "_approx_place_notes", []) or [])
        if missing_notes:
            note_text = "；".join(missing_notes)
            message = f"{message} {note_text}".strip() if message else note_text
            needs_clarification = True
            self.planner(city)._missing_place_notes.clear()
        if approx_notes:
            note_text = "；".join(approx_notes)
            message = f"{message} {note_text}".strip() if message else note_text
            self.planner(city)._approx_place_notes.clear()
        return {
            "message": message,
            "intent": intent.to_dict(),
            "plan": plan_payload,
            "alternatives": alternatives_payload,
            "preference": self.preference.to_dict(),
            "llm": llm_meta,
            "plan_updated": plan_updated,
            "needs_clarification": needs_clarification,
            "route_delta": self._route_delta(
                current,
                plan,
                intent,
                plan_updated=plan_updated,
                needs_clarification=needs_clarification,
            ),
        }

    def _chat_first_round(
        self,
        text: str,
        city: str,
        baseline: Intent,
        *,
        apply_realtime: bool,
        bootstrap_error: str,
    ) -> dict:
        result = self.chat(text, city, apply_realtime=apply_realtime, current_plan=None)
        result["llm"]["error"] = bootstrap_error
        result["llm"]["ops_fallback"] = True
        return result

    @staticmethod
    def _preserve_plan_days(intent: Intent, current: RoutePlan) -> Intent:
        """Keep multi-day structure when the follow-up utterance omits day count."""
        planned_days = max(1, int(getattr(current, "day_count", 1) or 1))
        if planned_days > 1 and intent.days <= 1:
            return replace(intent, days=planned_days)
        return intent

    @staticmethod
    def _incomplete_fixed_commitment(intent: Intent) -> bool:
        """Fixed-slot item with a time window but no place — ask, don't guess."""
        has_place = bool((intent.prefer_near or "").strip())
        has_window = intent.busy_from_hour is not None
        return has_window and not has_place

    @staticmethod
    def _soft_constraints_actionable(baseline: Intent, intent: Intent) -> bool:
        """True when soft fields justify replan; busy window alone without place does not."""
        if (intent.prefer_near or "").strip():
            return True
        if getattr(intent, "want_places", None):
            return True
        if intent.available_minutes != baseline.available_minutes:
            return True
        if intent.start_hour != baseline.start_hour:
            return True
        if intent.categories != baseline.categories:
            return True
        if (
            intent.mode != baseline.mode
            or intent.pace != baseline.pace
            or intent.scene != baseline.scene
        ):
            return True
        return False

    @staticmethod
    def _route_delta(
        previous: RoutePlan | None,
        plan: RoutePlan,
        intent: Intent,
        *,
        plan_updated: bool,
        needs_clarification: bool,
    ) -> dict[str, Any]:
        day_one = [stop for stop in plan.stops if stop.day_index == 1] or list(plan.stops)
        last = day_one[-1] if day_one else None
        added: list[str] = []
        if previous is not None:
            prev_ids = {stop.poi_id for stop in previous.stops}
            added = [stop.name for stop in plan.stops if stop.poi_id not in prev_ids][:6]
        kind = "new"
        if needs_clarification:
            kind = "clarify"
        elif previous is not None:
            kind = "updated" if plan_updated else "unchanged"
        return {
            "kind": kind,
            "added_names": added,
            "highlight_name": last.name if last else "",
            "prefer_near": (intent.prefer_near or "").strip(),
        }

    @staticmethod
    def _local_dynamic_message(text: str, current: RoutePlan) -> str:
        baseline = parse_local_intent(text, current.city)
        if utterance_is_vague_stop_replace(text) and current.stops:
            stop_names = "、".join(stop.name for stop in current.stops[:5])
            return (
                f"可以换成更对味的一站。你更想换掉现在这几站里的哪一个？"
                f"（{stop_names}）说一下站名就行，我再动那一站。"
            )
        if utterance_adds_indoor(text):
            return "好，室外那些先留着，我再帮你加一处凉快的室内点，整体不动大结构。"
        if utterance_switches_to_indoor(text):
            return "好，太热就少待室外，我按室内馆方向重排一版，尽量加进能避暑的站。"
        if utterance_is_artistic_style_shift(text):
            return "好，往更文艺、人少一点的街区方向调一版，经典站能留的先留着。"
        if utterance_wants_hutong(text):
            return "好，现有站先留着，我再往胡同老建筑方向加几站，不合适再调。"
        if baseline.prefer_near and (
            baseline.busy_from_hour is not None or "晚上" in text or "中午" in text
        ):
            return (
                f"明白，我会把那天想看的地方尽量安排在{baseline.prefer_near}附近，"
                "前后留出赶路缓冲，这样你赴约会更从容。"
            )
        if baseline.busy_from_hour is not None and not baseline.prefer_near:
            return (
                "好，你那天有一段固定安排。方便的话告诉我大概在哪个区域，"
                "我好把前后行程就近排，避免临时跨城赶。"
            )
        if any(token in text for token in ("室内", "下雨")):
            return "好，我先按更偏室内的方向，在现有路线上轻轻调一版。"
        if current.stops:
            return "好，我顺着你这句话，在现有路线上轻轻调一版。"
        return "好，我先按你的新想法整理一版。"

    @staticmethod
    def _local_dynamic_intent(baseline: Intent, text: str, current: RoutePlan) -> Intent:
        intent = baseline
        lowered = text.lower()
        if any(token in text for token in ("三小时", "3小时", "少一小时", "缩短", "时间不够")):
            minutes = 180 if "三" in text or "3" in text else max(90, int(current.available_minutes) - 60)
            intent = replace(intent, available_minutes=minutes)
        if utterance_adds_indoor(text):
            # ADD indoor categories; do not strip parks/outdoor from the intent.
            cats = list(intent.categories or [])
            for extra in ("museum", "culture", "heritage"):
                if extra not in cats:
                    cats.append(extra)
            intent = replace(intent, categories=cats)
        elif utterance_switches_to_indoor(text) or "室内" in text or "下雨" in lowered:
            merged = list(
                dict.fromkeys([*(intent.categories or []), "museum", "culture", "heritage"])
            )
            intent = replace(intent, categories=merged)
        if utterance_wants_hutong(text):
            cats = list(intent.categories or [])
            for extra in ("heritage", "culture", "attraction"):
                if extra not in cats:
                    cats.append(extra)
            intent = replace(
                intent,
                scene="历史文化游" if intent.scene != "城市漫游" else intent.scene,
                categories=cats,
            )
        if utterance_is_artistic_style_shift(text):
            cats = list(intent.categories or [])
            for extra in ("heritage", "culture", "attraction", "leisure"):
                if extra not in cats:
                    cats.append(extra)
            intent = replace(intent, scene="城市漫游", categories=cats)
        return intent

    @staticmethod
    def _apply_scenario_ops(
        text: str,
        current: RoutePlan,
        intent: Intent,
        used_ops: list[dict],
    ) -> tuple[Intent, list[dict]]:
        """Ensure strong transferable cues produce actionable ops (not talk-only)."""
        if utterance_is_vague_stop_replace(text) or not current.stops:
            return intent, used_ops
        scenario = TripSenseService._scenario_ops_for_utterance(text, current)
        if not scenario:
            return intent, used_ops
        intent = TripSenseService._local_dynamic_intent(intent, text, current)
        # Prefer scenario locks/prefer/avoid; keep unrelated LLM removes if named denials.
        denied = parse_denied_places(text)
        kept_removes = [
            op
            for op in (used_ops or [])
            if op.get("op") == "remove"
            and any(
                token in next(
                    (s.name for s in current.stops if s.poi_id == op.get("poi_id")),
                    "",
                )
                for token in denied
            )
        ]
        return intent, [*kept_removes, *scenario]

    @staticmethod
    def _scenario_ops_for_utterance(text: str, current: RoutePlan) -> list[dict]:
        """Transferable Phase-C ops for indoor / hutong / artistic cues."""
        if not current.stops:
            return []
        denied = parse_denied_places(text)
        ops: list[dict] = []
        for stop in current.stops:
            if any(token in stop.name for token in denied):
                ops.append({"op": "remove", "poi_id": stop.poi_id})

        if utterance_switches_to_indoor(text):
            # Switch (not add): do not lock outdoor parks — leave room for museums.
            ops.append(
                {
                    "op": "prefer_categories",
                    "categories": ["museum", "culture", "heritage"],
                }
            )
            ops.append({"op": "avoid_outdoor", "enabled": True})
            ops.append({"op": "replan", "scope": "all"})
            return ops

        if utterance_adds_indoor(text):
            outdoor_keep = [
                stop
                for stop in current.stops
                if stop.poi_type in {"park", "attraction"}
                or "公园" in stop.name
                or "坛" in stop.name
            ]
            keep = outdoor_keep or [
                stop
                for stop in current.stops
                if not any(token in stop.name for token in denied)
            ]
            if keep:
                ops.append({"op": "lock", "poi_ids": [stop.poi_id for stop in keep]})
            ops.append({"op": "replan", "scope": "tail"})
            ops.append(
                {
                    "op": "prefer_categories",
                    "categories": ["museum", "culture", "heritage", "park"],
                }
            )
            return ops

        if utterance_wants_hutong(text):
            # ADD hutong preference: keep existing parks/heritage unless denied.
            keep = [
                stop
                for stop in current.stops
                if not any(token in stop.name for token in denied)
            ]
            # Leave one unlocked slot when the day is already dense, so soft-seeded
            # hutong stops can enter; always keep 颐和园/恭王府/故宫 if present.
            priority = [
                stop
                for stop in keep
                if any(token in stop.name for token in ("颐和园", "恭王府", "故宫", "天坛"))
            ]
            prefix = keep[: max(2, len(keep) - 1)] if len(keep) >= 4 else list(keep)
            for stop in priority:
                if stop not in prefix:
                    prefix.append(stop)
            if prefix:
                ops.append({"op": "lock", "poi_ids": [stop.poi_id for stop in prefix]})
            ops.append(
                {
                    "op": "prefer_categories",
                    "categories": ["heritage", "culture", "attraction"],
                }
            )
            ops.append({"op": "replan", "scope": "tail"})
            return ops

        if utterance_is_artistic_style_shift(text):
            day_one = [stop for stop in current.stops if stop.day_index == 1] or list(
                current.stops
            )
            # Soft lock a short prefix; style shift may reshape the tail.
            keep = [
                stop
                for stop in day_one[: max(1, min(2, len(day_one) // 2 or 1))]
                if not any(token in stop.name for token in denied)
            ]
            if keep:
                ops.append({"op": "lock", "poi_ids": [stop.poi_id for stop in keep]})
            ops.append(
                {
                    "op": "prefer_categories",
                    "categories": ["heritage", "culture", "attraction", "leisure"],
                }
            )
            ops.append({"op": "replan", "scope": "tail"})
            return ops

        return ops

    @staticmethod
    def _local_dynamic_ops(text: str, current: RoutePlan) -> list[dict]:
        ops: list[dict] = []
        if not current.stops:
            return ops
        if utterance_is_vague_stop_replace(text):
            return ops
        scenario = TripSenseService._scenario_ops_for_utterance(text, current)
        if scenario:
            return scenario
        baseline = parse_local_intent(text, current.city)
        denied = parse_denied_places(text)
        for stop in current.stops:
            if any(token in stop.name for token in denied):
                ops.append({"op": "remove", "poi_id": stop.poi_id})
        # Need a place (or classic shorten/indoor) before reshuffling the route.
        if baseline.prefer_near:
            day_one = [stop for stop in current.stops if stop.day_index == 1] or list(
                current.stops
            )
            keep = [
                stop
                for stop in day_one[: max(1, min(2, len(day_one) // 2 or 1))]
                if not any(token in stop.name for token in denied)
            ]
            if keep:
                ops.append({"op": "lock", "poi_ids": [stop.poi_id for stop in keep]})
            ops.append({"op": "replan", "scope": "tail"})
            return ops
        if denied or baseline.want_places or any(
            token in text for token in ("三小时", "3小时", "少一小时", "缩短", "时间不够", "室内")
        ):
            keep = [
                stop
                for stop in current.stops[: max(1, len(current.stops) // 2)]
                if not any(token in stop.name for token in denied)
            ]
            if keep:
                ops.append({"op": "lock", "poi_ids": [stop.poi_id for stop in keep]})
            ops.append({"op": "replan", "scope": "tail"})
            if "室内" in text:
                ops.append(
                    {
                        "op": "prefer_categories",
                        "categories": ["museum", "culture", "heritage", "park"],
                    }
                )
        return ops

    def _search_named_place(self, name: str, city: str, intent: Intent) -> dict | None:
        from tripsense.knowledge.amap_place_search import search_place

        try:
            return search_place(name, city)
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
            return None

    def _supplement_named_place(self, name: str, city: str, intent: Intent) -> dict | None:
        fn = getattr(self.llm_provider, "supplement_named_place", None)
        if not callable(fn):
            return None
        try:
            return fn(name, city, intent)
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
            return None

    def _apply_llm_reasons(
        self, intent: Intent, plan: RoutePlan
    ) -> tuple[RoutePlan, dict]:
        evidence = {
            "mode": plan.mode,
            "scene": plan.scene,
            "available_minutes": plan.available_minutes,
            "planned_minutes": plan.planned_minutes,
            "voice": plan.voice,
            "stops": [
                {
                    "poi_id": stop.poi_id,
                    "name": stop.name,
                    "category": stop.category,
                    "district": stop.district,
                    "local_reason": stop.reason,
                    "evidence": stop.evidence[:3],
                }
                for stop in plan.stops
            ],
        }
        try:
            bundle = self.llm_provider.write_reasons(intent, evidence)
            return self._merge_reason_bundle(plan, bundle), {"fallback": False}
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            return plan, {
                "fallback": True,
                "error": str(exc)[:240] or type(exc).__name__,
            }

    @staticmethod
    def _merge_reason_bundle(plan: RoutePlan, bundle: LlmReasonBundle) -> RoutePlan:
        stops = []
        for stop in plan.stops:
            rewritten = bundle.stop_reasons.get(stop.poi_id)
            stops.append(replace(stop, reason=rewritten or stop.reason))
        return replace(
            plan,
            stops=stops,
            route_reason=bundle.route_reason or plan.route_reason or plan.voice,
        )

    def search_knowledge(
        self,
        text: str,
        city: str = "shanghai",
        *,
        limit: int = 20,
        apply_realtime: bool = False,
    ) -> dict:
        intent = parse_local_intent(text, city, apply_realtime=apply_realtime)
        planner = self.planner(city)
        if planner.knowledge is None:
            return {"intent": intent.to_dict(), "candidates": []}

        candidates = planner.knowledge.retrieve(intent, limit=limit)
        return {
            "intent": intent.to_dict(),
            "candidates": [
                {
                    "poi_id": item.poi.poi_id,
                    "name": item.poi.name,
                    "category": item.poi.category,
                    "poi_type": item.poi.poi_type,
                    "district": item.poi.district,
                    "address": item.poi.address,
                    "longitude": item.poi.longitude,
                    "latitude": item.poi.latitude,
                    "score": item.score,
                    "quality_flags": item.poi.quality_flags,
                    "opening_hours_verified": item.poi.opening_hours_verified,
                    "opening_windows": item.poi.opening_windows,
                    "stable_summary": (
                        item.stable_document.summary if item.stable_document else ""
                    ),
                    "evidence": [evidence.to_dict() for evidence in item.evidence],
                    "realtime_status": (
                        {
                            "open_status": item.realtime_state.open_status,
                            "crowd_level": item.realtime_state.crowd_level,
                            "reservation_status": item.realtime_state.reservation_status,
                            "transit_status": item.realtime_state.transit_status,
                            "message": item.realtime_state.message,
                        }
                        if item.realtime_state
                        else {"availability": "unknown"}
                    ),
                    "realtime_notices": [notice.to_dict() for notice in item.realtime_notices],
                }
                for item in candidates
            ],
            "preference": self.preference.to_dict(),
        }
