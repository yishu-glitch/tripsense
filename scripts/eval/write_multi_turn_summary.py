"""Generate Chinese multi-turn eval summary markdown from JSON reports."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def fmt_rate(value: float) -> str:
    return f"{100 * value:.1f}%"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# Keep in sync with docs/TripSense_评测与指标体系_v2.1.md §7 / 附录 A and metrics.py.
METRICS_LEGEND = """
## 附录：指标说明

完整说明见 [`docs/TripSense_评测与指标体系_v2.1.md`](../../../docs/TripSense_评测与指标体系_v2.1.md) **§7**（产品语言）与 **附录 A**（公式）；阈值以 `src/tripsense/eval/metrics.py` 为准。

### 首轮 vs 多轮

- **首轮**：只评用户第一句的初始规划；表中「调整合理性」为 `—`（还没有改线可评）。
- **多轮**：按对话依次改线，对**最终路线**打分；更能暴露缩短/加点/换点后的回退。
- 「本轮 / 修复前」是两次独立跑批的对比标签，不是同一次执行里的两个阶段。

### Case Pass（整案过线）vs soft

- **Case Pass** = 机会约束满足（硬）**且**综合分 ≥ 难度下限（简单 **0.70** / 中等 **0.75** / 困难 **0.80**）。
- 表中 CCP / density / theme / … 是各量纲**单项过线率**，不等于 Case Pass。
- **soft**：整案已过，但某一项未达单项过线门槛；写入失败列表并带 `[soft]`，用于归因，**不算** Case 失败。

### 各指标（单项过线门槛）

| 指标 | 产品含义 | 过线门槛 |
|------|----------|----------|
| 机会约束满足 (ccp) | 行程是否在安全时间预算内、不超负荷 | **1.0**（硬：必须满分） |
| 调整合理性 (adjust) | 改线是否跟用户请求同向 | **0.6** |
| 主题相关性 (theme) | 停靠是否贴合旅行主题 | **0.6**（文史/亲子 **0.7**） |
| 地理连贯 (geo) | 相邻景点会不会离谱远跳 | **0.6** |
| 停靠密度 (density) | 一天站数是否和可用时间匹配（别太稀也别太满） | **0.5** |
| 约束保留 (retain) | 半天/亲子/低行动力等硬要求改完后还在不在 | **0.7** |
| 综合分 (overall) | 六项加权综合质量 | 见上难度下限（整案门槛，非单项） |

### 常见失败码（产品语言）

| 码 | 含义 |
|----|------|
| `theme_mismatch` | 主题不够贴 |
| `too_sparse` / `overcrowded` | 停靠过稀 / 过密 |
| `constraint_dropped` | 硬约束丢失 |
| `ccp_violation` | 超出安全时间预算（硬） |
| `geo_jump` | 地理跳跃异常 |
| `op_noop` / `adjust_opposite` | 改线未生效 / 调整方向不对 |
""".strip()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    reports = root / "data" / "eval" / "reports"
    mt = load(reports / "eval_report_20260926_113228.json")
    ft = load(reports / "eval_report_20260926_113150.json")
    prev_mt = load(reports / "eval_report_20260926_100251.json")
    prev_ft = load(reports / "eval_report_20260926_100312.json")

    lines: list[str] = [
        "# TripSense 多轮评测摘要报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        "> 数据集：`data/eval/dialogue_eval_200.jsonl`（200 例）  ",
        "> 模式：规则 UserSim 回放 + `TripSenseService.chat` + 规则 Judge（`--judge stub`）  ",
        "> 原始 JSON：`eval_report_20260926_113228.json`",
        "",
        "## 1. 总览",
        "",
        "| 跑次 | Case Pass | CCP | density | theme | adjust | retain | geo |",
        "|------|-----------|-----|---------|-------|--------|--------|-----|",
        (
            f"| 多轮（本轮） | {mt['passed']}/{mt['total']}（{fmt_rate(mt['pass_rate'])}） | "
            f"{fmt_rate(mt['metric_pass_rates']['ccp'])} | "
            f"{fmt_rate(mt['metric_pass_rates']['density'])} | "
            f"{fmt_rate(mt['metric_pass_rates']['theme'])} | "
            f"{fmt_rate(mt['metric_pass_rates']['adjust'])} | "
            f"{fmt_rate(mt['metric_pass_rates']['retain'])} | "
            f"{fmt_rate(mt['metric_pass_rates']['geo'])} |"
        ),
        (
            f"| 多轮（修复前） | {prev_mt['passed']}/{prev_mt['total']}（{fmt_rate(prev_mt['pass_rate'])}） | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['ccp'])} | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['density'])} | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['theme'])} | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['adjust'])} | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['retain'])} | "
            f"{fmt_rate(prev_mt['metric_pass_rates']['geo'])} |"
        ),
        (
            f"| 首轮（本轮） | {ft['passed']}/{ft['total']}（{fmt_rate(ft['pass_rate'])}） | "
            f"{fmt_rate(ft['metric_pass_rates']['ccp'])} | "
            f"{fmt_rate(ft['metric_pass_rates']['density'])} | "
            f"{fmt_rate(ft['metric_pass_rates']['theme'])} | — | "
            f"{fmt_rate(ft['metric_pass_rates']['retain'])} | "
            f"{fmt_rate(ft['metric_pass_rates']['geo'])} |"
        ),
        (
            f"| 首轮（修复前） | {prev_ft['passed']}/{prev_ft['total']}（{fmt_rate(prev_ft['pass_rate'])}） | "
            f"{fmt_rate(prev_ft['metric_pass_rates']['ccp'])} | "
            f"{fmt_rate(prev_ft['metric_pass_rates']['density'])} | "
            f"{fmt_rate(prev_ft['metric_pass_rates']['theme'])} | — | "
            f"{fmt_rate(prev_ft['metric_pass_rates']['retain'])} | "
            f"{fmt_rate(prev_ft['metric_pass_rates']['geo'])} |"
        ),
        "",
        "说明：Case Pass 要求机会约束硬通过且综合分达难度下限；"
        "density/theme 等 soft 仍写入失败列表（`[soft]`）以便归因。"
        "字段释义见文末 [附录：指标说明](#附录指标说明) 与 "
        "[`TripSense_评测与指标体系_v2.1.md`](../../../docs/TripSense_评测与指标体系_v2.1.md) §7。",
        "",
        "## 2. 按操作类型（多标签）",
        "",
        "| ops tag | n | Case Pass |",
        "|---------|---|-----------|",
    ]
    for key, value in sorted(mt.get("by_op", {}).items()):
        lines.append(f"| `{key}` | {value['n']} | {fmt_rate(value['pass_rate'])} |")

    lines.extend(
        [
            "",
            "## 3. 按难度 / 场景",
            "",
            "### 难度",
            "",
            "| difficulty | n | Case Pass |",
            "|------------|---|-----------|",
        ]
    )
    for key, value in sorted(mt["by_difficulty"].items()):
        lines.append(f"| {key} | {value['n']} | {fmt_rate(value['pass_rate'])} |")

    lines.extend(
        [
            "",
            "### 场景",
            "",
            "| style_scene | n | Case Pass |",
            "|-------------|---|-----------|",
        ]
    )
    for key, value in sorted(mt["by_scene"].items()):
        lines.append(f"| {key} | {value['n']} | {fmt_rate(value['pass_rate'])} |")

    by_code: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for result in mt["results"]:
        for failure in result.get("failures") or []:
            for code in failure["failure_codes"]:
                by_code[code] += 1
                if len(examples[code]) < 8:
                    examples[code].append(result["case_id"])

    lines.extend(
        [
            "",
            "## 4. 失败归因聚类（含 soft）",
            "",
            "| failure_code | 次数 | 示例 case id |",
            "|--------------|------|-------------|",
        ]
    )
    for code, count in by_code.most_common():
        lines.append(f"| `{code}` | {count} | {', '.join(examples[code])} |")

    hard = [result for result in mt["results"] if not result["passed"]]
    lines.extend(["", "### 硬失败", ""])
    if not hard:
        lines.append("本轮 **无硬失败**（200/200 Case Pass）。")
    else:
        for result in hard:
            codes = sorted({c for f in result["failures"] for c in f["failure_codes"]})
            lines.append(
                f"- `{result['case_id']}` overall={result['overall']:.3f} codes={codes}"
            )

    lines.extend(
        [
            "",
            "### 主要 soft 簇解读",
            "",
            "1. **主题不够贴**：多轮最终停靠与首轮主题匹配偏弱"
            "（常见于休闲/亲子末轮偏商场或弱相关点）。主题分已改为对照首轮话术。",
            "2. **停靠过稀**：半天/公园/单点长停留等仍偏稀；密度选种修复已消除原先北京郊区"
            "「抗战纪念馆+卢沟桥」全日仅 2 站主簇（首轮 density 91.5%→99.0%）。",
            "3. **硬约束丢失 / 停靠过密**：个位数 soft，建议人工抽检。",
            "",
            "## 5. 本轮代码修复",
            "",
            "| 改动 | 说明 |",
            "|------|------|",
            "| `planner._density_aware_seed_id` | 避开 hop 孤立高分种子，缓解全日停靠过稀 |",
            "| `metrics.score_density` 归因 | 稀少不再误标为过密 |",
            "| `metrics.score_theme` | 多轮按首轮话术打主题分 |",
            "| `runner` 汇总 | 增加按操作类型切片；文档合并为评测与指标体系 v2.1 |",
            "",
            "## 6. 复跑命令",
            "",
            "```bash",
            "python -m tripsense.eval --smoke",
            "python -m tripsense.eval --first-turn-only",
            "python -m tripsense.eval --multi-turn --judge stub",
            "```",
            "",
            "报告目录：`data/eval/reports/`。",
            "",
            METRICS_LEGEND,
            "",
        ]
    )

    text = "\n".join(lines)
    stamped = reports / "multi_turn_summary_20260926.md"
    latest = reports / "multi_turn_summary_latest.md"
    stamped.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(f"wrote {stamped}")
    print(f"soft_codes={dict(by_code)}")


if __name__ == "__main__":
    main()
