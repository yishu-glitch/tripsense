"""Eval runner: load cases, call TripSenseService, emit score reports."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tripsense.config import load_project_env
from tripsense.core.service import TripSenseService
from tripsense.llm import llm_provider_from_env

from .judge import RuleJudgeAgent, StubLlmJudge, merge_llm_overlay
from .loaders import default_eval_path, load_eval_cases, repo_root
from .regression import attribution_summary, write_zh_attribution_notes
from .schema import EvalCaseResult, FailureAttribution, ScoreReport
from .usersim import ScriptUserSimAgent


_OP_TAGS = ("add_stop", "shorten", "replace", "state_change")


def _stop_names(plan: Any) -> list[str]:
    return [str(stop.name) for stop in getattr(plan, "stops", []) or []]


def _trace_row(
    *,
    turn: int,
    user: str,
    plan: Any,
    result: EvalCaseResult,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    codes: list[str] = []
    for failure in result.failures:
        codes.extend(failure.failure_codes)
    metric_bits = {m.name: {"score": m.score, "passed": m.passed, "detail": m.detail} for m in result.metrics}
    intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
    want_places = [str(name) for name in (intent.get("want_places") or []) if name]
    return {
        "turn": turn,
        "user": user,
        "assistant": str(payload.get("message") or ""),
        "stops": _stop_names(plan),
        "passed": result.passed,
        "overall": result.overall,
        "failure_codes": list(dict.fromkeys(codes)),
        "metrics": metric_bits,
        "plan_updated": payload.get("plan_updated"),
        "needs_clarification": payload.get("needs_clarification"),
        "scene": getattr(plan, "scene", ""),
        "mode": getattr(plan, "mode", ""),
        "want_places": want_places,
    }


def format_turn_traces_markdown(results: list[EvalCaseResult], *, title: str = "逐轮对话与路线") -> str:
    lines = [f"# {title}", ""]
    if not results:
        lines.append("（没有结果）")
        return "\n".join(lines)
    for result in results:
        lines.append(f"## {result.case_id} · {'Pass' if result.passed else 'Fail'} · overall {result.overall:.2f}")
        lines.append("")
        if not result.turn_trace:
            names = (result.plan_summary or {}).get("stops") or []
            lines.append("本条报告没有逐轮轨迹，仅有终局：" + (" → ".join(names) if names else "（空）"))
            lines.append("")
            continue
        for row in result.turn_trace:
            lines.append(f"### T{row.get('turn')}")
            lines.append("")
            lines.append(f"- **用户：** {row.get('user') or '（空）'}")
            reply = str(row.get('assistant') or "").strip()
            if reply:
                lines.append(f"- **系统：** {reply}")
            stops = row.get("stops") or []
            lines.append(f"- **当时路线：** {' → '.join(stops) if stops else '（空）'}")
            scene = str(row.get("scene") or "").strip()
            mode = str(row.get("mode") or "").strip()
            if scene or mode:
                lines.append(f"- **场景/节奏：** {scene or '—'} / {mode or '—'}")
            want_places = [str(name) for name in (row.get("want_places") or []) if name]
            if want_places:
                lines.append(f"- **点名景点：** {', '.join(want_places)}")
            codes = row.get("failure_codes") or []
            status = "过" if row.get("passed") else "挂"
            extra = f"；{', '.join(codes)}" if codes else ""
            lines.append(f"- **当轮分数：** {status} · overall {float(row.get('overall') or 0):.2f}{extra}")
            theme = (row.get("metrics") or {}).get("theme") or {}
            if theme:
                lines.append(f"- **主题：** {theme.get('detail') or theme.get('score')}")
            if row.get("needs_clarification"):
                lines.append("- **本轮未改线（在澄清）**")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _make_service(*, use_llm: bool = False) -> TripSenseService:
    if not use_llm:
        return TripSenseService()
    load_project_env()
    return TripSenseService(llm_provider=llm_provider_from_env())


def _summarize(results: list[EvalCaseResult]) -> ScoreReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    metric_totals: dict[str, list[bool]] = defaultdict(list)
    by_scene: dict[str, list[bool]] = defaultdict(list)
    by_diff: dict[str, list[bool]] = defaultdict(list)
    by_op: dict[str, list[bool]] = defaultdict(list)
    code_counts: Counter[str] = Counter()

    for result in results:
        by_scene[result.style_scene].append(result.passed)
        by_diff[result.difficulty].append(result.passed)
        tagged = False
        for op in _OP_TAGS:
            if op in result.tags:
                by_op[op].append(result.passed)
                tagged = True
        if not tagged:
            by_op["(none)"].append(result.passed)
        for metric in result.metrics:
            metric_totals[metric.name].append(metric.passed)
        for failure in result.failures:
            for code in failure.failure_codes:
                code_counts[code] += 1

    def rate(flags: list[bool]) -> float:
        return (sum(flags) / len(flags)) if flags else 0.0

    return ScoreReport(
        total=total,
        passed=passed,
        pass_rate=rate([r.passed for r in results]),
        metric_pass_rates={k: rate(v) for k, v in sorted(metric_totals.items())},
        by_scene={k: {"n": len(v), "pass_rate": rate(v)} for k, v in sorted(by_scene.items())},
        by_difficulty={k: {"n": len(v), "pass_rate": rate(v)} for k, v in sorted(by_diff.items())},
        by_op={k: {"n": len(v), "pass_rate": rate(v)} for k, v in sorted(by_op.items())},
        failure_code_counts=dict(code_counts),
        attribution=attribution_summary(results),
        results=results,
    )


def _plan_first_turn(
    service: TripSenseService,
    case: dict[str, Any],
    utterance: str,
) -> Any:
    return service.plan(utterance, city=str(case.get("city") or "shanghai"), apply_realtime=False)


def run_case_first_turn(
    service: TripSenseService,
    case: dict[str, Any],
    judge: RuleJudgeAgent,
    llm_judge: StubLlmJudge | None = None,
) -> EvalCaseResult:
    dialogue = case.get("dialogue") or []
    utterance = str(dialogue[0].get("user") if dialogue else "")
    try:
        plan = _plan_first_turn(service, case, utterance)
        if not plan.stops:
            return EvalCaseResult(
                case_id=case["id"],
                city=str(case.get("city")),
                style_scene=str(case.get("style_scene")),
                difficulty=str(case.get("difficulty")),
                tags=list(case.get("tags") or []),
                overall=0.0,
                passed=False,
                failures=[
                    FailureAttribution(
                        case_id=case["id"],
                        turn=1,
                        failed_metrics=["theme", "density"],
                        failure_codes=["empty_plan"],
                        stage="plan",
                        evidence={"user_utterance": utterance},
                        hypothesis="planner returned no stops",
                        suggested_fix_area="planner|scoring|data",
                        primary_cause="algorithm",
                        secondary_cause="knowledge_data",
                    )
                ],
                error="empty_plan",
            )
        result = judge.evaluate_plan(case, utterance, plan, turn=1, stage="plan", active_tags=[])
        if llm_judge is not None:
            result = merge_llm_overlay(result, llm_judge.judge(case, utterance, plan))
        result.turn_trace = [
            _trace_row(turn=1, user=utterance, plan=plan, result=result)
        ]
        return result
    except Exception as exc:  # noqa: BLE001 - eval harness must continue
        return EvalCaseResult(
            case_id=case["id"],
            city=str(case.get("city")),
            style_scene=str(case.get("style_scene")),
            difficulty=str(case.get("difficulty")),
            tags=list(case.get("tags") or []),
            overall=0.0,
            passed=False,
            failures=[
                FailureAttribution(
                    case_id=case["id"],
                    turn=1,
                    failed_metrics=[],
                    failure_codes=["runtime_error"],
                    stage="plan",
                    evidence={"error": str(exc)[:300]},
                    hypothesis="exception during plan()",
                    suggested_fix_area="service|planner",
                    primary_cause="other",
                    secondary_cause="",
                )
            ],
            error=str(exc)[:300],
        )


def run_case_multi_turn(
    service: TripSenseService,
    case: dict[str, Any],
    judge: RuleJudgeAgent,
    usersim: ScriptUserSimAgent,
    llm_judge: StubLlmJudge | None = None,
) -> EvalCaseResult:
    """Replay scripted turns via chat(); score the final plan (and CCP each step)."""
    turns = usersim.turns_for_case(case)
    if not turns:
        return run_case_first_turn(service, case, judge, llm_judge)

    current_plan: dict[str, Any] | None = None
    before_plan = None
    last_result: EvalCaseResult | None = None
    pref_state = None
    turn_trace: list[dict[str, Any]] = []

    for i, turn in enumerate(turns):
        try:
            payload = service.chat(
                turn.user,
                city=str(case.get("city") or "shanghai"),
                apply_realtime=False,
                current_plan=current_plan,
                preference_state=pref_state,
            )
            pref_state = payload.get("preference") or payload.get("preference_state")
            plan_dict = payload.get("plan") or {}
            from tripsense.core.plan_ops import route_plan_from_dict

            plan = route_plan_from_dict(plan_dict) if plan_dict.get("stops") else None
            if plan is None:
                raise ValueError("chat returned empty plan")
            active = turn.tags if i > 0 else []
            result = judge.evaluate_plan(
                case,
                turn.user,
                plan,
                before=before_plan,
                turn=turn.turn,
                stage="plan" if i == 0 else "replan",
                active_tags=active,
            )
            if llm_judge is not None:
                result = merge_llm_overlay(
                    result,
                    llm_judge.judge(case, turn.user, plan, before=before_plan),
                )
            turn_trace.append(
                _trace_row(
                    turn=turn.turn,
                    user=turn.user,
                    plan=plan,
                    result=result,
                    payload=payload,
                )
            )
            last_result = result
            before_plan = plan
            current_plan = payload.get("plan") or plan.to_dict()
        except Exception as exc:  # noqa: BLE001
            return EvalCaseResult(
                case_id=case["id"],
                city=str(case.get("city")),
                style_scene=str(case.get("style_scene")),
                difficulty=str(case.get("difficulty")),
                tags=list(case.get("tags") or []),
                overall=0.0,
                passed=False,
                failures=[
                    FailureAttribution(
                        case_id=case["id"],
                        turn=turn.turn,
                        failed_metrics=[],
                        failure_codes=["runtime_error"],
                        stage="replan" if i else "plan",
                        evidence={"error": str(exc)[:300], "user_utterance": turn.user},
                        hypothesis="exception during chat()/replan",
                        suggested_fix_area="service|plan_ops",
                        primary_cause="other",
                        secondary_cause="",
                    )
                ],
                error=str(exc)[:300],
            )

    assert last_result is not None
    last_result.turn_trace = turn_trace
    return last_result


def run_eval(
    *,
    path: Path | None = None,
    limit: int | None = None,
    ids: set[str] | None = None,
    multi_turn: bool = False,
    use_llm_judge: bool = False,
    use_llm: bool = False,
) -> ScoreReport:
    cases = load_eval_cases(path, ids=ids, limit=limit)
    service = _make_service(use_llm=use_llm)
    judge = RuleJudgeAgent(llm_enabled=use_llm)
    llm_judge = StubLlmJudge() if use_llm_judge else None
    usersim = ScriptUserSimAgent()
    results: list[EvalCaseResult] = []
    for case in cases:
        if multi_turn:
            results.append(run_case_multi_turn(service, case, judge, usersim, llm_judge))
        else:
            results.append(run_case_first_turn(service, case, judge, llm_judge))
    return _summarize(results)


def run_smoke(limit: int = 40, *, use_llm: bool = False) -> ScoreReport:
    """Stratified smoke (~40). With --llm, uses chat() multi-turn path for soft prior/ops."""
    cases = load_eval_cases()
    if not cases:
        raise FileNotFoundError(f"No eval cases at {default_eval_path()}")
    syn = [c for c in cases if str(c["id"]).startswith("syn-")]
    rest = [c for c in cases if not str(c["id"]).startswith("syn-")]
    step = max(1, len(rest) // max(1, limit - len(syn)))
    subset = rest[::step][: max(0, limit - len(syn))] + syn
    subset = subset[:limit]
    service = _make_service(use_llm=use_llm)
    judge = RuleJudgeAgent(llm_enabled=use_llm)
    if use_llm:
        usersim = ScriptUserSimAgent()
        results = [run_case_multi_turn(service, case, judge, usersim, None) for case in subset]
    else:
        results = [run_case_first_turn(service, case, judge) for case in subset]
    return _summarize(results)


def write_report(report: ScoreReport, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (repo_root() / "data" / "eval" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"eval_report_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "eval_report_latest.json"
    latest.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    notes = write_zh_attribution_notes(report, out_dir / f"attribution_notes_{stamp}.md")
    latest_notes = out_dir / "attribution_notes_latest.md"
    latest_notes.write_text(notes.read_text(encoding="utf-8"), encoding="utf-8")
    traced = [item for item in report.results if item.turn_trace]
    focus = [item for item in traced if (not item.passed) or item.failures] or traced
    if focus:
        trace_md = format_turn_traces_markdown(focus, title="评测逐轮对话与路线")
        (out_dir / f"turn_traces_{stamp}.md").write_text(trace_md, encoding="utf-8")
        (out_dir / "turn_traces_latest.md").write_text(trace_md, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TripSense dialogue eval runner")
    parser.add_argument("--path", type=Path, default=None, help="Eval JSONL path")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", type=str, default="", help="Comma-separated case ids")
    parser.add_argument("--smoke", action="store_true", help="Run stratified first-turn smoke")
    parser.add_argument("--multi-turn", action="store_true")
    parser.add_argument("--judge", choices=["stub", "none"], default="none")
    parser.add_argument("--split", choices=["all", "smoke"], default="all")
    parser.add_argument("--first-turn-only", action="store_true", help="Alias for not --multi-turn")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable product LLM from .env (soft prior / plan ops / reasons)",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    ids = {x.strip() for x in args.ids.split(",") if x.strip()} or None
    if args.smoke or args.split == "smoke":
        report = run_smoke(limit=args.limit or 40, use_llm=args.llm)
    else:
        report = run_eval(
            path=args.path,
            limit=args.limit,
            ids=ids,
            multi_turn=args.multi_turn and not args.first_turn_only,
            use_llm_judge=args.judge == "stub",
            use_llm=args.llm,
        )
    out = write_report(report, args.out)
    summary = {
        "total": report.total,
        "passed": report.passed,
        "pass_rate": round(report.pass_rate, 4),
        "metric_pass_rates": {k: round(v, 4) for k, v in report.metric_pass_rates.items()},
        "by_scene": report.by_scene,
        "by_difficulty": report.by_difficulty,
        "by_op": report.by_op,
        "failure_code_counts": report.failure_code_counts,
        "attribution": report.attribution,
        "llm_enabled": bool(args.llm),
        "report_path": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.pass_rate >= 0.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
