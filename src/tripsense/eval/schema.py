"""Schemas for eval cases, scores, and failure attribution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


FAILURE_CODES = (
    "ccp_violation",
    "theme_mismatch",
    "geo_jump",
    "overcrowded",
    "too_sparse",
    "constraint_dropped",
    "adjust_opposite",
    "intent_parse_miss",
    "op_noop",
    "empty_plan",
    "runtime_error",
)

# Product-facing root-cause taxonomy for fail/weak-case review.
ATTRIBUTION_CATEGORIES = (
    "prompt",
    "algorithm",
    "knowledge_data",
    "rag_architecture",
    "case_design",
    "other",
)


def infer_root_cause(
    failure_codes: list[str],
    *,
    stage: str = "plan",
    llm_enabled: bool = False,
    soft: bool = False,
) -> tuple[str, str]:
    """Map failure codes → (primary, secondary) attribution categories.

    Prefer a single primary; secondary only when clearly useful.
    Do not blame prompts for pure CCP/geo/density bugs (or vice versa).
    """
    codes = set(failure_codes or [])
    if not codes:
        return "other", ""

    if "runtime_error" in codes:
        return "other", ""

    if codes & {"ccp_violation", "geo_jump", "overcrowded", "too_sparse"}:
        # Feasibility / routing density is planner responsibility.
        secondary = "knowledge_data" if "theme_mismatch" in codes else ""
        return "algorithm", secondary

    if "constraint_dropped" in codes:
        # Hard constraints should survive replan; intent/ops wording is secondary
        # only when an LLM actually drove the patch.
        return "algorithm", "prompt" if llm_enabled and stage == "replan" else ""

    if codes & {"op_noop", "adjust_opposite"}:
        if llm_enabled and stage in {"replan", "plan"}:
            return "prompt", "algorithm"
        return "algorithm", ""

    if "theme_mismatch" in codes:
        # Offline: local scene/category scoring + POI labels.
        # With soft-prior LLM: wrong scene/weights may be prompt-driven.
        if llm_enabled and stage == "plan":
            return "prompt", "knowledge_data"
        return "algorithm", "knowledge_data"

    if "intent_parse_miss" in codes:
        return ("prompt", "algorithm") if llm_enabled else ("algorithm", "")

    if "empty_plan" in codes:
        return "algorithm", "knowledge_data"

    if soft:
        return "algorithm", ""
    return "other", ""


@dataclass(slots=True)
class FailureAttribution:
    case_id: str
    turn: int
    failed_metrics: list[str]
    failure_codes: list[str]
    stage: str  # plan | replan | judge
    evidence: dict[str, Any] = field(default_factory=dict)
    hypothesis: str = ""
    suggested_fix_area: str = ""
    regression: bool = False
    primary_cause: str = ""
    secondary_cause: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MetricScore:
    name: str
    score: float
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvalCaseResult:
    case_id: str
    city: str
    style_scene: str
    difficulty: str
    tags: list[str]
    overall: float
    passed: bool
    metrics: list[MetricScore] = field(default_factory=list)
    failures: list[FailureAttribution] = field(default_factory=list)
    plan_summary: dict[str, Any] = field(default_factory=dict)
    turn_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "city": self.city,
            "style_scene": self.style_scene,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "overall": self.overall,
            "passed": self.passed,
            "metrics": [m.to_dict() for m in self.metrics],
            "failures": [f.to_dict() for f in self.failures],
            "plan_summary": self.plan_summary,
            "turn_trace": self.turn_trace,
            "error": self.error,
        }


@dataclass(slots=True)
class ScoreReport:
    total: int
    passed: int
    pass_rate: float
    metric_pass_rates: dict[str, float]
    by_scene: dict[str, dict[str, float]]
    by_difficulty: dict[str, dict[str, float]]
    failure_code_counts: dict[str, int]
    by_op: dict[str, dict[str, float]] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)
    results: list[EvalCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "metric_pass_rates": self.metric_pass_rates,
            "by_scene": self.by_scene,
            "by_difficulty": self.by_difficulty,
            "by_op": self.by_op,
            "failure_code_counts": self.failure_code_counts,
            "attribution": self.attribution,
            "results": [r.to_dict() for r in self.results],
        }
