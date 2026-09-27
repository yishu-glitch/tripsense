"""Regression loop helpers: fail → attribute → hypothesis → re-run subset."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .schema import ATTRIBUTION_CATEGORIES, EvalCaseResult, ScoreReport


def cluster_failures(results: list[EvalCaseResult], *, include_soft: bool = False) -> dict[str, Any]:
    by_code: Counter[str] = Counter()
    by_area: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for result in results:
        if result.passed and not include_soft:
            continue
        for failure in result.failures:
            if result.passed and not str(failure.hypothesis).startswith("[soft]"):
                continue
            if (not result.passed) or include_soft:
                for code in failure.failure_codes:
                    by_code[code] += 1
                    if len(examples[code]) < 5:
                        examples[code].append(result.case_id)
                if failure.suggested_fix_area:
                    by_area[failure.suggested_fix_area] += 1
    return {
        "failure_codes": dict(by_code),
        "fix_areas": dict(by_area),
        "examples": dict(examples),
    }


def attribution_summary(results: list[EvalCaseResult]) -> dict[str, Any]:
    """Cluster hard fails + soft metric misses by primary_cause taxonomy."""
    hard_by_cause: Counter[str] = Counter()
    soft_by_cause: Counter[str] = Counter()
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)

    for result in results:
        for failure in result.failures:
            cause = failure.primary_cause or "other"
            soft = bool(result.passed) or str(failure.hypothesis).startswith("[soft]")
            bucket = soft_by_cause if soft and result.passed else hard_by_cause
            bucket[cause] += 1
            if len(examples[cause]) < 3:
                codes = ",".join(failure.failure_codes)
                hyp = failure.hypothesis[:120]
                examples[cause].append(
                    {
                        "case_id": result.case_id,
                        "kind": "soft" if result.passed else "hard",
                        "codes": codes,
                        "hypothesis": hyp,
                        "secondary": failure.secondary_cause or "",
                    }
                )

    return {
        "taxonomy": list(ATTRIBUTION_CATEGORIES),
        "hard_primary_counts": {k: hard_by_cause.get(k, 0) for k in ATTRIBUTION_CATEGORIES},
        "soft_primary_counts": {k: soft_by_cause.get(k, 0) for k in ATTRIBUTION_CATEGORIES},
        "examples": dict(examples),
        "note": (
            "hard= case overall fail; soft= metric miss while overall still passes. "
            "Offline (no --llm) rarely attributes to prompt."
        ),
    }


def failed_case_ids(report: ScoreReport) -> list[str]:
    return [r.case_id for r in report.results if not r.passed]


def neighbor_ids(
    all_cases: list[dict[str, Any]],
    seed_ids: list[str],
    *,
    limit: int = 20,
) -> list[str]:
    """Expand a fail subset with same scene/tag neighbors for regression."""
    by_id = {c["id"]: c for c in all_cases}
    seeds = [by_id[i] for i in seed_ids if i in by_id]
    chosen: list[str] = list(seed_ids)
    seen = set(chosen)
    for seed in seeds:
        scene = seed.get("style_scene")
        tags = set(seed.get("tags") or [])
        for case in all_cases:
            if case["id"] in seen:
                continue
            if case.get("style_scene") != scene:
                continue
            if tags and not (tags & set(case.get("tags") or [])):
                continue
            chosen.append(case["id"])
            seen.add(case["id"])
            if len(chosen) >= limit:
                return chosen
    return chosen


def write_attribution_bundle(
    report: ScoreReport,
    out_path: Path,
    *,
    hypotheses: dict[str, str] | None = None,
) -> Path:
    """Persist structured attributions for human review before architecture edits."""
    hypotheses = hypotheses or {}
    failures: list[dict[str, Any]] = []
    for result in report.results:
        for failure in result.failures:
            payload = failure.to_dict()
            if result.case_id in hypotheses:
                payload["hypothesis"] = hypotheses[result.case_id]
            failures.append(payload)
    bundle = {
        "pass_rate": report.pass_rate,
        "clusters": cluster_failures(report.results, include_soft=True),
        "attribution": report.attribution or attribution_summary(report.results),
        "failed_ids": failed_case_ids(report),
        "attributions": failures,
        "loop": [
            "1. 人工审阅 attributions（看 primary_cause / secondary_cause）。",
            "2. 确认修复假设，仅改动对应类别模块（勿把算法问题改成提示词）。",
            "3. 复测：python -m tripsense.eval --ids <失败+邻域>",
            "4. 对比 pass_rate 与 failure_code_counts / attribution；门禁看 ccp + overall。",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def write_zh_attribution_notes(report: ScoreReport, out_path: Path) -> Path:
    """Write a short Chinese attribution summary for product review."""
    attr = report.attribution or attribution_summary(report.results)
    hard = attr.get("hard_primary_counts") or {}
    soft = attr.get("soft_primary_counts") or {}
    examples = attr.get("examples") or {}
    lines = [
        "# Eval 失败/弱例归因摘要",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 用例数：{report.total}；通过：{report.passed}；pass_rate={report.pass_rate:.4f}",
        f"- 失败码计数：{json.dumps(report.failure_code_counts, ensure_ascii=False)}",
        "",
        "## 归因分类（primary）",
        "",
        "| 类别 | hard 失败次数 | soft 弱例次数 |",
        "|------|--------------|--------------|",
    ]
    for cat in ATTRIBUTION_CATEGORIES:
        lines.append(f"| `{cat}` | {hard.get(cat, 0)} | {soft.get(cat, 0)} |")
    lines.extend(["", "## 各类示例", ""])
    for cat in ATTRIBUTION_CATEGORIES:
        items = examples.get(cat) or []
        if not items:
            continue
        lines.append(f"### {cat}")
        for item in items:
            lines.append(
                f"- `{item.get('case_id')}` ({item.get('kind')}): "
                f"{item.get('codes')} — {item.get('hypothesis')}"
            )
        lines.append("")
    lines.extend(
        [
            "## 说明",
            "",
            "- `prompt`：LLM 提示词 / 阶段契约 / 软先验 / PlanOps 措辞",
            "- `algorithm`：CCP / beam / dwell / geo / scoring / planner / replan",
            "- `knowledge_data`：知识库内容、上海 CSV 标签、类型覆盖、POI 元数据",
            "- `rag_architecture`：检索分层与证据注入方式",
            "- `case_design`：评测用例本身不公/过时/期望不合理",
            "- `other`：基础设施、API 抖动、接线缺陷等",
            "",
            "离线默认（无 `--llm`）几乎不会把问题归到 `prompt`；开启 `--llm` 后 PlanOps/软先验相关失败才可能标为 prompt。",
            "",
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# Outline consumed by docs / operators（中文操作指引）
REGRESSION_LOOP_OUTLINE = """
失败 → 归因 → 假设 → 子集复测
1. python -m tripsense.eval --smoke
2. 查看 data/eval/reports/eval_report_latest.json 与 attribution_notes_latest.md
3. 用 tripsense.eval.regression.attribution_summary / cluster_failures 聚类
4. 人工确认架构修改点（禁止自动改架构落地；勿混淆 prompt vs algorithm）
5. python -m tripsense.eval --ids id1,id2,...
6. 需要覆盖 ops 时加 --multi-turn --judge stub；需要产品 LLM 时加 --llm
""".strip()
