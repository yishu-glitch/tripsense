from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Intent, RoutePlan, Stop

ALLOWED_PLAN_OPS = {
    "lock",
    "remove",
    "prefer_categories",
    "avoid_outdoor",
    "replan",
}
ALLOWED_CATEGORIES = {"attraction", "heritage", "park", "museum", "culture", "leisure"}
ALLOWED_ATTR_WEIGHTS = {
    "cultural_score",
    "nature_score",
    "commercial_score",
    "scenic_score",
    "indoor_score",
    "edu_score",
}


@dataclass(slots=True)
class SoftPlanningPrior:
    assistant_reply: str = ""
    intent_patch: dict[str, Any] = field(default_factory=dict)
    preference_weights: dict[str, float] = field(default_factory=dict)
    prefer_tags: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanOpsProposal:
    assistant_reply: str = ""
    intent_patch: dict[str, Any] = field(default_factory=dict)
    ops: list[dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = False
    needs_clarification: bool = False


@dataclass(slots=True)
class AlternativeChoice:
    chosen_profile_id: str
    assistant_reply: str = ""
    reject_profile_ids: list[str] = field(default_factory=list)


def route_plan_from_dict(payload: dict[str, Any]) -> RoutePlan:
    stops = []
    for raw in payload.get("stops") or []:
        if not isinstance(raw, dict) or not raw.get("poi_id"):
            continue
        stops.append(
            Stop(
                poi_id=str(raw["poi_id"]),
                name=str(raw.get("name", raw["poi_id"])),
                category=str(raw.get("category", "")),
                poi_type=str(raw.get("poi_type", "")),
                district=str(raw.get("district", "")),
                dwell_minutes=float(raw.get("dwell_minutes") or 60),
                travel_to_next_minutes=(
                    float(raw["travel_to_next_minutes"])
                    if raw.get("travel_to_next_minutes") is not None
                    else None
                ),
                score=float(raw.get("score") or 0.0),
                reason=str(raw.get("reason") or ""),
                completed=bool(raw.get("completed", False)),
                actual_dwell_minutes=(
                    float(raw["actual_dwell_minutes"])
                    if raw.get("actual_dwell_minutes") is not None
                    else None
                ),
                evidence=list(raw.get("evidence") or []),
                realtime_status=dict(raw.get("realtime_status") or {}),
                day_index=int(raw.get("day_index") or 1),
                arrival_time=(
                    str(raw["arrival_time"]) if raw.get("arrival_time") else None
                ),
            )
        )
    if not stops:
        raise ValueError("current_plan must include at least one stop")
    return RoutePlan(
        city=str(payload.get("city") or "shanghai"),
        mode=str(payload.get("mode") or "balanced"),
        scene=str(payload.get("scene") or "综合观光游"),
        stops=stops,
        available_minutes=float(payload.get("available_minutes") or 240),
        safe_budget_minutes=float(payload.get("safe_budget_minutes") or 200),
        planned_minutes=float(payload.get("planned_minutes") or 0),
        satisfaction_probability=float(payload.get("satisfaction_probability") or 0.0),
        reminder_level=str(payload.get("reminder_level") or "comfortable"),
        voice=str(payload.get("voice") or ""),
        realtime_notices=list(payload.get("realtime_notices") or []),
        route_reason=str(payload.get("route_reason") or ""),
        uncertainty_minutes=float(payload.get("uncertainty_minutes") or 30.0),
        risk_epsilon=float(payload.get("risk_epsilon") or 0.1),
        risk_model=str(payload.get("risk_model") or "gaussian"),
        chance_constraint_satisfied=bool(
            payload.get("chance_constraint_satisfied", True)
        ),
        day_count=int(payload.get("day_count") or 1),
    )


def apply_intent_patch(intent: Intent, patch: dict[str, Any] | None) -> Intent:
    if not patch:
        return intent
    from dataclasses import replace

    kwargs: dict[str, Any] = {}
    if patch.get("mode") in {"balanced", "relaxed", "full", "deep", "photo"}:
        kwargs["mode"] = patch["mode"]
    if patch.get("scene") in {
        "综合观光游",
        "历史文化游",
        "自然公园游",
        "城市漫游",
        "休闲购物游",
        "亲子研学游",
    }:
        kwargs["scene"] = patch["scene"]
    if patch.get("pace") in {"slow", "normal", "fast"}:
        kwargs["pace"] = patch["pace"]
    if isinstance(patch.get("mood"), str) and patch["mood"].strip():
        kwargs["mood"] = str(patch["mood"])[:60]
    if isinstance(patch.get("categories"), list):
        cats = [str(item) for item in patch["categories"] if item in ALLOWED_CATEGORIES]
        if cats:
            kwargs["categories"] = cats
    minutes = patch.get("available_minutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
        kwargs["available_minutes"] = max(60, min(720, round(minutes)))
    days = patch.get("days")
    if isinstance(days, (int, float)) and not isinstance(days, bool):
        kwargs["days"] = max(1, min(7, int(days)))
    if isinstance(patch.get("start_name"), str) and patch["start_name"].strip():
        kwargs["start_name"] = str(patch["start_name"]).strip()[:40]
    start_hour = patch.get("start_hour")
    if isinstance(start_hour, (int, float)) and not isinstance(start_hour, bool):
        kwargs["start_hour"] = max(6.0, min(21.0, float(start_hour)))
    if isinstance(patch.get("prefer_near"), str) and patch["prefer_near"].strip():
        kwargs["prefer_near"] = str(patch["prefer_near"]).strip()[:40]
    if isinstance(patch.get("want_places"), list):
        from .intent import sanitize_want_places

        places = sanitize_want_places(patch.get("want_places"))
        if places:
            kwargs["want_places"] = places
    for key in ("busy_from_hour", "busy_until_hour"):
        raw = patch.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            kwargs[key] = max(6.0, min(23.0, float(raw)))
    return replace(intent, **kwargs) if kwargs else intent


def validate_preference_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    weights: dict[str, float] = {}
    for key, value in raw.items():
        if key not in ALLOWED_ATTR_WEIGHTS:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            weights[str(key)] = float(max(0.05, min(2.0, value)))
    return weights


def validate_plan_ops(
    raw_ops: Any, *, allowed_poi_ids: set[str], apply_realtime: bool
) -> list[dict[str, Any]]:
    if not isinstance(raw_ops, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in raw_ops:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op", ""))
        if op not in ALLOWED_PLAN_OPS:
            continue
        if op == "lock":
            poi_ids = [
                str(poi_id)
                for poi_id in (item.get("poi_ids") or [])
                if str(poi_id) in allowed_poi_ids
            ]
            if poi_ids:
                cleaned.append({"op": "lock", "poi_ids": poi_ids})
        elif op == "remove":
            poi_id = str(item.get("poi_id") or "")
            if poi_id in allowed_poi_ids:
                cleaned.append({"op": "remove", "poi_id": poi_id})
        elif op == "prefer_categories":
            cats = [
                str(cat)
                for cat in (item.get("categories") or [])
                if cat in ALLOWED_CATEGORIES
            ]
            if cats:
                cleaned.append({"op": "prefer_categories", "categories": cats})
        elif op == "avoid_outdoor":
            # Product gate: only affect selection after user confirms realtime.
            cleaned.append(
                {"op": "avoid_outdoor", "enabled": bool(item.get("enabled")) and apply_realtime}
            )
        elif op == "replan":
            scope = str(item.get("scope") or "all")
            if scope not in {"all", "tail"}:
                scope = "all"
            cleaned.append({"op": "replan", "scope": scope})
    return cleaned
