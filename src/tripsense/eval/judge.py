"""Judge / Eval agent hooks (rule primary, LLM optional stub)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tripsense.core.models import RoutePlan

from .metrics import score_plan
from .prompts import prompt_judge
from .schema import EvalCaseResult, FailureAttribution, MetricScore, infer_root_cause


JUDGE_CHECKLIST = prompt_judge()


class LlmJudge(Protocol):
    def judge(
        self,
        case: dict[str, Any],
        utterance: str,
        plan: RoutePlan,
        *,
        before: RoutePlan | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class RuleJudgeAgent:
    """Maps to TripSenseService/planner outputs via deterministic rubrics."""

    llm_enabled: bool = False

    def evaluate_plan(
        self,
        case: dict[str, Any],
        utterance: str,
        plan: RoutePlan,
        *,
        before: RoutePlan | None = None,
        turn: int = 1,
        stage: str = "plan",
        active_tags: list[str] | None = None,
    ) -> EvalCaseResult:
        metrics, overall, passed, failures = score_plan(
            plan,
            case=case,
            utterance=utterance,
            before=before,
            turn=turn,
            stage=stage,
            active_tags=active_tags,
            llm_enabled=self.llm_enabled,
        )
        return EvalCaseResult(
            case_id=case["id"],
            city=str(case.get("city") or plan.city),
            style_scene=str(case.get("style_scene") or plan.scene),
            difficulty=str(case.get("difficulty") or "medium"),
            tags=list(case.get("tags") or []),
            overall=overall,
            passed=passed,
            metrics=metrics,
            failures=failures,
            plan_summary={
                "n_stops": len(plan.stops),
                "planned_minutes": plan.planned_minutes,
                "safe_budget_minutes": plan.safe_budget_minutes,
                "chance_constraint_satisfied": plan.chance_constraint_satisfied,
                "scene": plan.scene,
                "mode": plan.mode,
                "stops": [s.name for s in plan.stops],
            },
        )


class StubLlmJudge:
    """No-op LLM judge; returns empty overlay so harness runs offline."""

    system_prompt = staticmethod(prompt_judge)

    def judge(
        self,
        case: dict[str, Any],
        utterance: str,
        plan: RoutePlan,
        *,
        before: RoutePlan | None = None,
    ) -> dict[str, Any]:
        return {
            "scores": {},
            "pass": True,
            "failure_codes": [],
            "notes": "llm_judge_stub_skipped",
            "prompt_spec": JUDGE_CHECKLIST[:120],
        }


def merge_llm_overlay(
    result: EvalCaseResult,
    overlay: dict[str, Any],
) -> EvalCaseResult:
    """Optionally blend LLM judge failure codes into the rule result."""
    codes = list(overlay.get("failure_codes") or [])
    if not codes:
        return result
    if overlay.get("pass") is False:
        result.passed = False
        primary, secondary = infer_root_cause(codes, stage="judge", llm_enabled=True)
        result.failures.append(
            FailureAttribution(
                case_id=result.case_id,
                turn=0,
                failed_metrics=["llm_judge"],
                failure_codes=codes,
                stage="judge",
                evidence={"notes": overlay.get("notes", "")},
                hypothesis=str(overlay.get("notes") or "llm_judge_failed"),
                suggested_fix_area="llm_reasons|planner",
                primary_cause=primary or "prompt",
                secondary_cause=secondary or "algorithm",
            )
        )
    return result
