from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tripsense.core.intent import sanitize_want_places
from tripsense.core.models import Intent
from tripsense.core.plan_ops import (
    AlternativeChoice,
    PlanOpsProposal,
    SoftPlanningPrior,
    validate_plan_ops,
    validate_preference_weights,
)

from . import prompts

ALLOWED_MODES = {"balanced", "relaxed", "full", "deep", "photo"}
ALLOWED_SCENES = {
    "综合观光游",
    "历史文化游",
    "自然公园游",
    "城市漫游",
    "休闲购物游",
    "亲子研学游",
}
ALLOWED_CATEGORIES = {"attraction", "heritage", "park", "museum", "culture", "leisure"}
ALLOWED_PACES = {"slow", "normal", "fast"}


@dataclass(slots=True)
class LlmInterpretation:
    mode: str | None = None
    scene: str | None = None
    categories: list[str] | None = None
    mood: str | None = None
    pace: str | None = None
    available_minutes: int | None = None
    assistant_reply: str = ""


@dataclass(slots=True)
class LlmReasonBundle:
    route_reason: str
    stop_reasons: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LlmDiaryDayRewrite:
    day_index: int
    title: str = ""
    narrative: str = ""


@dataclass(slots=True)
class LlmDiaryRewrite:
    title: str
    subtitle: str
    days: list[LlmDiaryDayRewrite] = field(default_factory=list)


class LlmProvider(Protocol):
    provider_name: str
    model_name: str

    def interpret(self, text: str, baseline: Intent) -> LlmInterpretation: ...

    def propose_soft_prior(self, text: str, baseline: Intent) -> SoftPlanningPrior: ...

    def propose_plan_ops(
        self,
        text: str,
        baseline: Intent,
        current_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        apply_realtime: bool = False,
    ) -> PlanOpsProposal: ...

    def choose_alternative(
        self,
        text: str,
        baseline: Intent,
        alternatives: list[dict[str, Any]],
    ) -> AlternativeChoice: ...

    def write_reasons(
        self, intent: Intent, evidence: dict[str, Any]
    ) -> LlmReasonBundle: ...

    def rewrite_diary(self, local_diary: dict[str, Any]) -> LlmDiaryRewrite: ...


class UnavailableLlmProvider:
    provider_name = "local-baseline"
    model_name = "local-intent-v1"

    def interpret(self, text: str, baseline: Intent) -> LlmInterpretation:
        raise RuntimeError("external LLM is not configured")

    def propose_soft_prior(self, text: str, baseline: Intent) -> SoftPlanningPrior:
        raise RuntimeError("external LLM is not configured")

    def propose_plan_ops(
        self,
        text: str,
        baseline: Intent,
        current_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        apply_realtime: bool = False,
    ) -> PlanOpsProposal:
        raise RuntimeError("external LLM is not configured")

    def choose_alternative(
        self,
        text: str,
        baseline: Intent,
        alternatives: list[dict[str, Any]],
    ) -> AlternativeChoice:
        raise RuntimeError("external LLM is not configured")

    def write_reasons(
        self, intent: Intent, evidence: dict[str, Any]
    ) -> LlmReasonBundle:
        raise RuntimeError("external LLM is not configured")

    def rewrite_diary(self, local_diary: dict[str, Any]) -> LlmDiaryRewrite:
        raise RuntimeError("external LLM is not configured")

    def supplement_named_place(
        self, name: str, city: str, baseline: Intent
    ) -> dict[str, Any]:
        raise RuntimeError("external LLM is not configured")


def _post_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300].strip()
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"LLM network error: {exc.reason}") from exc


class OpenAICompatibleLlmProvider:
    provider_name = "openai-compatible"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        transport: Callable[
            [str, dict[str, str], dict[str, Any], float], dict[str, Any]
        ] = _post_json,
        timeout_seconds: float = 18.0,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model_name = model.strip()
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def interpret(self, text: str, baseline: Intent) -> LlmInterpretation:
        data = self._complete_json(
            prompts.prompt_intent(),
            {
                "city": baseline.city,
                "user_text": text,
                "local_baseline": baseline.to_dict(),
            },
            max_tokens=650,
        )
        return _validated_interpretation(data)

    def propose_soft_prior(self, text: str, baseline: Intent) -> SoftPlanningPrior:
        data = self._complete_json(
            prompts.prompt_soft_prior(),
            {
                "city": baseline.city,
                "user_text": text,
                "local_baseline": baseline.to_dict(),
            },
            max_tokens=900,
        )
        return _validated_soft_prior(data)

    def propose_plan_ops(
        self,
        text: str,
        baseline: Intent,
        current_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        apply_realtime: bool = False,
    ) -> PlanOpsProposal:
        data = self._complete_json(
            prompts.prompt_plan_ops(),
            {
                "user_text": text,
                "local_baseline": baseline.to_dict(),
                "current_plan": {
                    "stops": [
                        {
                            "poi_id": stop.get("poi_id"),
                            "name": stop.get("name"),
                            "category": stop.get("category"),
                        }
                        for stop in (current_plan.get("stops") or [])
                    ],
                    "available_minutes": current_plan.get("available_minutes"),
                    "planned_minutes": current_plan.get("planned_minutes"),
                    "mode": current_plan.get("mode"),
                    "scene": current_plan.get("scene"),
                },
                "candidates": candidates[:12],
                "apply_realtime": apply_realtime,
            },
            max_tokens=900,
        )
        return _validated_plan_ops(data, current_plan, candidates, apply_realtime)

    def choose_alternative(
        self,
        text: str,
        baseline: Intent,
        alternatives: list[dict[str, Any]],
    ) -> AlternativeChoice:
        data = self._complete_json(
            prompts.prompt_choose_alternative(),
            {
                "user_text": text,
                "local_baseline": baseline.to_dict(),
                "alternatives": alternatives,
            },
            max_tokens=550,
        )
        return _validated_alternative_choice(data, alternatives)

    def write_reasons(
        self, intent: Intent, evidence: dict[str, Any]
    ) -> LlmReasonBundle:
        data = self._complete_json(
            prompts.prompt_reasons(),
            {"intent": intent.to_dict(), "evidence": evidence},
            max_tokens=900,
        )
        return _validated_reasons(data, evidence)

    def supplement_named_place(
        self, name: str, city: str, baseline: Intent
    ) -> dict[str, Any]:
        data = self._complete_json(
            prompts.prompt_supplement_place(),
            {
                "city": city,
                "place_name": name,
                "local_baseline": baseline.to_dict() if hasattr(baseline, "to_dict") else {},
            },
            max_tokens=280,
        )
        if not isinstance(data, dict):
            raise TypeError("LLM place supplement must be an object")
        data.setdefault("name", name)
        data.setdefault("city", city)
        data.pop("poi_id", None)
        data.pop("opentime", None)
        data.pop("opening_hours", None)
        data.pop("ticket", None)
        data.pop("tel", None)
        return data

    def rewrite_diary(self, local_diary: dict[str, Any]) -> LlmDiaryRewrite:
        safe_input = {
            "title": local_diary.get("title"),
            "subtitle": local_diary.get("subtitle"),
            "route": local_diary.get("route"),
            "moods": local_diary.get("moods"),
            "days": [
                {
                    "day_index": day.get("day_index"),
                    "title": day.get("title"),
                    "entries": [
                        {
                            "place_name": entry.get("place_name"),
                            "mood": entry.get("mood"),
                            "note": entry.get("note"),
                            "photo_count": len(entry.get("photo_refs") or []),
                        }
                        for entry in day.get("entries") or []
                    ],
                }
                for day in local_diary.get("days") or []
            ],
        }
        data = self._complete_json(
            prompts.prompt_diary(),
            safe_input,
            max_tokens=900,
            timeout_seconds=max(self.timeout_seconds, 30.0),
        )
        return _validated_diary_rewrite(data, local_diary)

    def _complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            payload = self.transport(
                f"{self.base_url}/chat/completions",
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                {
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(user_payload, ensure_ascii=False),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
                timeout,
            )
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300].strip()
            except (OSError, ValueError, AttributeError):
                detail = str(exc.reason)
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM network error: {exc.reason}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("LLM did not return usable JSON") from exc
        if not isinstance(data, dict):
            raise TypeError("LLM JSON must be an object")
        return data


def _intent_system_prompt() -> str:
    return prompts.prompt_intent()


def _reasons_system_prompt() -> str:
    return prompts.prompt_reasons()


def _diary_system_prompt() -> str:
    return prompts.prompt_diary()


def _clip_zh(text: str, max_chars: int) -> str:
    value = " ".join(str(text).split())
    return value[:max_chars].strip()


def _extract_commitment_fields(data: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if isinstance(data.get("prefer_near"), str) and data["prefer_near"].strip():
        patch["prefer_near"] = str(data["prefer_near"]).strip()[:40]
    places = sanitize_want_places(data.get("want_places"))
    if places:
        patch["want_places"] = places
    for key in ("busy_from_hour", "busy_until_hour", "start_hour"):
        raw = data.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            hi = 23.0 if key.startswith("busy") else 21.0
            patch[key] = max(6.0, min(hi, float(raw)))
    return patch


def _validated_soft_prior(data: dict[str, Any]) -> SoftPlanningPrior:
    reply = data.get("assistant_reply")
    reply = str(reply).strip()[:180] if isinstance(reply, str) else ""
    if not reply:
        raise ValueError("LLM soft prior missing assistant_reply")
    patch = data.get("intent_patch") if isinstance(data.get("intent_patch"), dict) else {}
    # Reuse interpretation validator for whitelist fields inside patch / top-level.
    merged = dict(patch)
    for key in (
        "mode",
        "scene",
        "categories",
        "mood",
        "pace",
        "available_minutes",
        "want_places",
        "prefer_near",
    ):
        if key in data and key not in merged:
            merged[key] = data[key]
    interpretation = _validated_interpretation({**merged, "assistant_reply": reply})
    intent_patch: dict[str, Any] = {}
    if interpretation.mode:
        intent_patch["mode"] = interpretation.mode
    if interpretation.scene:
        intent_patch["scene"] = interpretation.scene
    if interpretation.categories:
        intent_patch["categories"] = interpretation.categories
    if interpretation.mood:
        intent_patch["mood"] = interpretation.mood
    if interpretation.pace:
        intent_patch["pace"] = interpretation.pace
    if interpretation.available_minutes:
        intent_patch["available_minutes"] = interpretation.available_minutes
    intent_patch.update(_extract_commitment_fields(merged))
    prefer_tags = [
        str(tag)[:20]
        for tag in (data.get("prefer_tags") or [])
        if isinstance(tag, str) and tag.strip()
    ][:6]
    avoid_tags = [
        str(tag)[:20]
        for tag in (data.get("avoid_tags") or [])
        if isinstance(tag, str) and tag.strip()
    ][:6]
    return SoftPlanningPrior(
        assistant_reply=reply,
        intent_patch=intent_patch,
        preference_weights=validate_preference_weights(data.get("preference_weights")),
        prefer_tags=prefer_tags,
        avoid_tags=avoid_tags,
    )


def _validated_plan_ops(
    data: dict[str, Any],
    current_plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    apply_realtime: bool,
) -> PlanOpsProposal:
    reply = data.get("assistant_reply")
    reply = str(reply).strip()[:180] if isinstance(reply, str) else ""
    if not reply:
        raise ValueError("LLM plan ops missing assistant_reply")
    allowed_ids = {
        str(stop.get("poi_id"))
        for stop in (current_plan.get("stops") or [])
        if stop.get("poi_id")
    }
    allowed_ids |= {
        str(item.get("poi_id")) for item in candidates if item.get("poi_id")
    }
    patch = data.get("intent_patch") if isinstance(data.get("intent_patch"), dict) else {}
    interpretation = _validated_interpretation({**patch, "assistant_reply": reply})
    intent_patch: dict[str, Any] = {}
    if interpretation.mode:
        intent_patch["mode"] = interpretation.mode
    if interpretation.scene:
        intent_patch["scene"] = interpretation.scene
    if interpretation.categories:
        intent_patch["categories"] = interpretation.categories
    if interpretation.pace:
        intent_patch["pace"] = interpretation.pace
    if interpretation.available_minutes:
        intent_patch["available_minutes"] = interpretation.available_minutes
    intent_patch.update(_extract_commitment_fields(patch))
    return PlanOpsProposal(
        assistant_reply=reply,
        intent_patch=intent_patch,
        ops=validate_plan_ops(
            data.get("ops"),
            allowed_poi_ids=allowed_ids,
            apply_realtime=apply_realtime,
        ),
        needs_confirmation=bool(data.get("needs_confirmation")),
        needs_clarification=bool(data.get("needs_clarification")),
    )


def _validated_alternative_choice(
    data: dict[str, Any], alternatives: list[dict[str, Any]]
) -> AlternativeChoice:
    allowed = {str(item.get("profile_id")) for item in alternatives if item.get("profile_id")}
    chosen = str(data.get("chosen_profile_id") or "")
    if chosen not in allowed:
        raise ValueError("LLM chose an unknown alternative profile_id")
    reply = data.get("assistant_reply")
    reply = str(reply).strip()[:180] if isinstance(reply, str) else ""
    if not reply:
        raise ValueError("LLM alternative choice missing assistant_reply")
    rejected = [
        str(item)
        for item in (data.get("reject_profile_ids") or [])
        if str(item) in allowed and str(item) != chosen
    ]
    return AlternativeChoice(
        chosen_profile_id=chosen,
        assistant_reply=reply,
        reject_profile_ids=rejected,
    )


def _validated_interpretation(data: dict[str, Any]) -> LlmInterpretation:
    mode = data.get("mode") if data.get("mode") in ALLOWED_MODES else None
    scene = data.get("scene") if data.get("scene") in ALLOWED_SCENES else None
    pace = data.get("pace") if data.get("pace") in ALLOWED_PACES else None
    raw_categories = data.get("categories")
    categories = None
    if isinstance(raw_categories, list):
        categories = [str(item) for item in raw_categories if item in ALLOWED_CATEGORIES]
        categories = categories or None
    raw_minutes = data.get("available_minutes")
    minutes = None
    if isinstance(raw_minutes, (int, float)) and not isinstance(raw_minutes, bool):
        minutes = max(60, min(720, round(raw_minutes)))
    mood = data.get("mood")
    mood = str(mood)[:60] if isinstance(mood, str) and mood.strip() else None
    reply = data.get("assistant_reply")
    reply = str(reply).strip()[:180] if isinstance(reply, str) else ""
    if not reply:
        raise ValueError("LLM response did not include assistant_reply")
    return LlmInterpretation(
        mode=mode,
        scene=scene,
        categories=categories,
        mood=mood,
        pace=pace,
        available_minutes=minutes,
        assistant_reply=reply,
    )


def _validated_reasons(
    data: dict[str, Any], evidence: dict[str, Any]
) -> LlmReasonBundle:
    route_reason = data.get("route_reason")
    if not isinstance(route_reason, str) or not route_reason.strip():
        raise ValueError("LLM reasons missing route_reason")
    route_reason = _clip_zh(route_reason, 48)
    allowed_ids = {
        str(item.get("poi_id"))
        for item in evidence.get("stops") or []
        if item.get("poi_id")
    }
    raw_stops = data.get("stop_reasons")
    stop_reasons: dict[str, str] = {}
    if isinstance(raw_stops, dict):
        for poi_id, reason in raw_stops.items():
            key = str(poi_id)
            if key not in allowed_ids or not isinstance(reason, str):
                continue
            clipped = _clip_zh(reason, 36)
            if clipped:
                stop_reasons[key] = clipped
    if allowed_ids and not stop_reasons:
        raise ValueError("LLM reasons did not include usable stop_reasons")
    return LlmReasonBundle(route_reason=route_reason, stop_reasons=stop_reasons)


def _validated_diary_rewrite(
    data: dict[str, Any], local_diary: dict[str, Any]
) -> LlmDiaryRewrite:
    title = data.get("title")
    subtitle = data.get("subtitle")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("LLM diary missing title")
    if not isinstance(subtitle, str) or not subtitle.strip():
        raise ValueError("LLM diary missing subtitle")
    allowed_days: set[int] = set()
    for day in local_diary.get("days") or []:
        raw = day.get("day_index")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        allowed_days.add(int(raw))

    days: list[LlmDiaryDayRewrite] = []
    raw_days = data.get("days")
    if isinstance(raw_days, list):
        for item in raw_days:
            if not isinstance(item, dict):
                continue
            raw_index = item.get("day_index")
            if isinstance(raw_index, bool) or not isinstance(raw_index, (int, float)):
                continue
            day_index = int(raw_index)
            if day_index not in allowed_days:
                continue
            day_title = item.get("title")
            narrative = item.get("narrative")
            days.append(
                LlmDiaryDayRewrite(
                    day_index=day_index,
                    title=_clip_zh(day_title, 40) if isinstance(day_title, str) else "",
                    narrative=(
                        _clip_zh(narrative, 220) if isinstance(narrative, str) else ""
                    ),
                )
            )
    return LlmDiaryRewrite(
        title=_clip_zh(title, 48),
        subtitle=_clip_zh(subtitle, 60),
        days=days,
    )


def llm_provider_from_env() -> LlmProvider:
    api_key = os.getenv("TRIPSENSE_LLM_API_KEY", "").strip()
    provider = os.getenv("TRIPSENSE_LLM_PROVIDER", "openai-compatible").strip().lower()
    base_url = os.getenv("TRIPSENSE_LLM_BASE_URL", "").strip()
    model = os.getenv("TRIPSENSE_LLM_MODEL", "").strip()
    if provider != "openai-compatible" or not all((api_key, base_url, model)):
        return UnavailableLlmProvider()
    return OpenAICompatibleLlmProvider(api_key, base_url, model)
