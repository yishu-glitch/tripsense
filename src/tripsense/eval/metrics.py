"""Deterministic scoring rubrics for planner outputs."""

from __future__ import annotations

from typing import Any

from tripsense.core.intent import parse_local_intent
from tripsense.core.models import RoutePlan

from .schema import FailureAttribution, MetricScore, infer_root_cause

OVERALL_WEIGHTS = {
    "ccp": 0.25,
    "adjust": 0.20,
    "theme": 0.20,
    "geo": 0.15,
    "density": 0.10,
    "retain": 0.10,
}

PASS_FLOOR = {
    "ccp": 1.0,
    "adjust": 0.6,
    "theme": 0.6,
    "geo": 0.6,
    "density": 0.5,
    "retain": 0.7,
}

OVERALL_PASS = {"low": 0.70, "medium": 0.75, "high": 0.80}

HERITAGE_HINTS = (
    "老建筑",
    "老洋房",
    "石库门",
    "弄堂",
    "故居",
    "外滩",
    "武康",
    "博物馆",
    "古迹",
    "heritage",
    "museum",
    "culture",
)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_ccp(plan: RoutePlan) -> MetricScore:
    ok = bool(plan.chance_constraint_satisfied) and (
        float(plan.planned_minutes) <= float(plan.safe_budget_minutes) + 1e-6
    )
    detail = (
        f"planned={plan.planned_minutes:.1f} safe={plan.safe_budget_minutes:.1f} "
        f"flag={plan.chance_constraint_satisfied}"
    )
    return MetricScore("ccp", 1.0 if ok else 0.0, ok, detail)


def score_theme(
    plan: RoutePlan,
    utterance: str,
    city: str,
    must_scene: str | None,
    *,
    theme_utterance: str | None = None,
) -> MetricScore:
    # Multi-turn: score theme against the planning opener (or must_scene), not
    # the latest shorten/state_change line which often has no scene keywords.
    theme_text = (theme_utterance or utterance or "").strip() or utterance
    intent = parse_local_intent(theme_text, city)
    scene = must_scene or intent.scene
    focus = set(intent.focus_terms or [])
    cats = set(intent.categories or [])
    if not plan.stops:
        return MetricScore("theme", 0.0, False, "empty_plan")

    hits = 0
    for stop in plan.stops:
        blob = f"{stop.name} {stop.category} {stop.poi_type}".lower()
        cat_hit = stop.category in cats or any(c in blob for c in cats)
        focus_hit = any(term in stop.name or term in blob for term in focus)
        heritage_hit = scene == "历史文化游" and any(h.lower() in blob for h in HERITAGE_HINTS)
        family_hit = scene == "亲子研学游" and any(
            k in blob for k in ("museum", "park", "culture", "博物馆", "公园")
        )
        if cat_hit or focus_hit or heritage_hit or family_hit:
            hits += 1
    ratio = hits / len(plan.stops)
    score = _clip(ratio / 0.5)
    floor = 0.7 if scene in {"历史文化游", "亲子研学游"} else PASS_FLOOR["theme"]
    return MetricScore("theme", score, score >= floor, f"hit_ratio={ratio:.2f} scene={scene}")


def score_geo(plan: RoutePlan) -> MetricScore:
    edges = [s.travel_to_next_minutes for s in plan.stops[:-1] if s.travel_to_next_minutes is not None]
    if not edges:
        return MetricScore("geo", 1.0, True, "single_or_missing_travel")
    anomalies = sum(1 for t in edges if float(t) > 90)
    score = 1.0 - anomalies / len(edges)
    return MetricScore(
        "geo",
        score,
        score >= PASS_FLOOR["geo"],
        f"anomalies={anomalies}/{len(edges)} max={max(edges):.1f}",
    )


def score_density(plan: RoutePlan, family_half_day: bool = False) -> MetricScore:
    n = len(plan.stops)
    expected = int(round(float(plan.available_minutes) / 90.0))
    expected = max(2, min(8, expected))
    if family_half_day:
        expected = min(expected, 4)
    if expected <= 0:
        expected = 3
    score = 1.0 - min(1.0, abs(n - expected) / expected)
    code = ""
    if n > expected + 1:
        code = "overcrowded"
    elif n < expected - 1:
        code = "too_sparse"
    elif n < expected:
        # Mild under-fill still attributes as sparse when score fails the floor.
        code = "too_sparse" if score < PASS_FLOOR["density"] else ""
    elif n > expected:
        code = "overcrowded" if score < PASS_FLOOR["density"] else ""
    else:
        code = ""
    return MetricScore(
        "density",
        score,
        score >= PASS_FLOOR["density"],
        f"stops={n} expected~{expected} {code}".strip(),
    )


def score_retain(plan: RoutePlan, constraints: dict[str, Any]) -> MetricScore:
    hard = list(constraints.get("hard") or [])
    if not hard:
        return MetricScore("retain", 1.0, True, "no_hard_constraints")
    penalties = 0
    notes: list[str] = []
    if "half_day_budget" in hard:
        if float(plan.available_minutes) > 270 and float(plan.planned_minutes) > 270:
            penalties += 1
            notes.append("half_day_exceeded")
    if "low_mobility" in hard:
        travels = [
            float(s.travel_to_next_minutes)
            for s in plan.stops[:-1]
            if s.travel_to_next_minutes is not None
        ]
        if travels and (sum(travels) / len(travels)) > 55:
            penalties += 1
            notes.append("high_avg_travel")
        if len(plan.stops) > 5:
            penalties += 1
            notes.append("too_many_stops_mobility")
    if "family_friendly" in hard and not plan.stops:
        penalties += 1
        notes.append("empty_family_plan")
    score = _clip(1.0 - penalties / max(1, len(hard)))
    return MetricScore(
        "retain",
        score,
        score >= PASS_FLOOR["retain"],
        ",".join(notes) or "ok",
    )


def score_adjust(
    before: RoutePlan | None,
    after: RoutePlan,
    tags: list[str],
) -> MetricScore:
    """Rule-based adjustment reasonableness; 1.0 when no multi-turn tags."""
    if not tags or before is None:
        return MetricScore("adjust", 1.0, True, "first_turn_or_no_tags")

    before_ids = [s.poi_id for s in before.stops]
    after_ids = [s.poi_id for s in after.stops]
    notes: list[str] = []
    score = 1.0

    if "shorten" in tags:
        shorter = len(after_ids) < len(before_ids) or after.planned_minutes < before.planned_minutes - 5
        if not shorter:
            score -= 0.35
            notes.append("shorten_not_reflected")
    if "add_stop" in tags:
        if len(after_ids) < len(before_ids):
            # allowed if CCP forced compaction
            if after.chance_constraint_satisfied and before.chance_constraint_satisfied:
                notes.append("add_stop_but_shrunk_for_ccp")
            else:
                score -= 0.25
                notes.append("add_stop_shrunk")
        elif set(after_ids) == set(before_ids):
            score -= 0.3
            notes.append("add_stop_noop")
    if "replace" in tags:
        if set(after_ids) == set(before_ids):
            score -= 0.4
            notes.append("replace_noop")
        else:
            # duration should stay roughly stable unless also shorten
            if "shorten" not in tags and before.planned_minutes > 0:
                drift = abs(after.planned_minutes - before.planned_minutes) / before.planned_minutes
                if drift > 0.35:
                    score -= 0.2
                    notes.append("replace_duration_drift")
    if "state_change" in tags:
        # relaxed signal: fewer stops or lower planned minutes often OK
        if len(after_ids) > len(before_ids) + 1:
            score -= 0.2
            notes.append("state_change_expanded")

    score = _clip(score)
    return MetricScore("adjust", score, score >= PASS_FLOOR["adjust"], ",".join(notes) or "ok")


def aggregate_scores(
    metrics: list[MetricScore],
    *,
    difficulty: str,
    multi_turn: bool,
) -> tuple[float, bool]:
    weights = dict(OVERALL_WEIGHTS)
    if not multi_turn:
        # renormalize without adjust/retain penalty paths (they are set to 1.0 already)
        pass
    overall = sum(weights[m.name] * m.score for m in metrics if m.name in weights)
    ccp = next(m for m in metrics if m.name == "ccp")
    floor = OVERALL_PASS.get(difficulty, 0.75)
    passed = ccp.passed and overall >= floor
    return overall, passed


def build_failures(
    case_id: str,
    turn: int,
    stage: str,
    metrics: list[MetricScore],
    evidence: dict[str, Any],
    *,
    llm_enabled: bool = False,
    soft: bool = False,
) -> list[FailureAttribution]:
    failed = [m for m in metrics if not m.passed]
    if not failed:
        return []
    codes: list[str] = []
    for m in failed:
        if m.name == "ccp":
            codes.append("ccp_violation")
        elif m.name == "theme":
            codes.append("theme_mismatch")
        elif m.name == "geo":
            codes.append("geo_jump")
        elif m.name == "density":
            if "too_sparse" in m.detail:
                codes.append("too_sparse")
            elif "overcrowded" in m.detail:
                codes.append("overcrowded")
            else:
                # Parse stops vs expected from detail when code tag is absent.
                codes.append("too_sparse" if "stops=" in m.detail else "overcrowded")
        elif m.name == "retain":
            codes.append("constraint_dropped")
        elif m.name == "adjust":
            if "noop" in m.detail:
                codes.append("op_noop")
            else:
                codes.append("adjust_opposite")
    uniq_codes = sorted(set(codes))
    area = "planner"
    if "intent_parse_miss" in uniq_codes:
        area = "intent"
    elif any(c in uniq_codes for c in ("op_noop", "adjust_opposite")):
        area = "plan_ops|replan"
    elif "ccp_violation" in uniq_codes:
        area = "constraints|planner"
    elif "theme_mismatch" in uniq_codes:
        area = "scoring|poi_metadata"
    primary, secondary = infer_root_cause(
        uniq_codes,
        stage=stage,
        llm_enabled=llm_enabled,
        soft=soft,
    )
    return [
        FailureAttribution(
            case_id=case_id,
            turn=turn,
            failed_metrics=[m.name for m in failed],
            failure_codes=uniq_codes,
            stage=stage,
            evidence=evidence,
            hypothesis="; ".join(m.detail for m in failed if m.detail),
            suggested_fix_area=area,
            primary_cause=primary,
            secondary_cause=secondary,
        )
    ]


def score_plan(
    plan: RoutePlan,
    *,
    case: dict[str, Any],
    utterance: str,
    before: RoutePlan | None = None,
    turn: int = 1,
    stage: str = "plan",
    active_tags: list[str] | None = None,
    llm_enabled: bool = False,
) -> tuple[list[MetricScore], float, bool, list[FailureAttribution]]:
    constraints = case.get("expected_constraints") or {}
    hard = constraints.get("hard") or []
    family_half = "family_friendly" in hard and (
        "half_day_budget" in hard or "半天" in utterance
    )
    tags = active_tags if active_tags is not None else list(case.get("tags") or [])
    multi_turn = before is not None and bool(tags)
    dialogue = case.get("dialogue") or []
    first_user = str(dialogue[0].get("user") or "") if dialogue else ""
    theme_utterance = first_user or utterance

    metrics = [
        score_ccp(plan),
        score_adjust(before, plan, tags if multi_turn else []),
        score_theme(
            plan,
            utterance,
            case.get("city") or plan.city,
            constraints.get("must_scene"),
            theme_utterance=theme_utterance,
        ),
        score_geo(plan),
        score_density(plan, family_half_day=family_half),
        score_retain(plan, constraints),
    ]
    overall, passed = aggregate_scores(
        metrics,
        difficulty=str(case.get("difficulty") or "medium"),
        multi_turn=multi_turn,
    )
    evidence = {
        "planned_minutes": plan.planned_minutes,
        "safe_budget_minutes": plan.safe_budget_minutes,
        "n_stops": len(plan.stops),
        "stop_names": [s.name for s in plan.stops],
        "user_utterance": utterance,
        "scene": plan.scene,
        "mode": plan.mode,
        "llm_enabled": llm_enabled,
    }
    # Always attribute soft metric misses so reports stay actionable even when
    # weighted overall still clears the case threshold.
    failures = build_failures(
        case["id"],
        turn,
        stage,
        metrics,
        evidence,
        llm_enabled=llm_enabled,
        soft=passed,
    )
    if passed:
        for failure in failures:
            failure.regression = False
            failure.hypothesis = f"[soft] {failure.hypothesis}"
    return metrics, overall, passed, failures
