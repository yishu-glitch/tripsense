# -*- coding: utf-8 -*-
"""Compare quality rerun vs prior badcase_rerun_20260926."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = Path(__file__).resolve().parent
BEFORE = json.loads(
    (ROOT / "data/eval/reports/badcase_rerun_20260926/eval_report_latest.json").read_text(
        encoding="utf-8"
    )
)
AFTER = json.loads((OUT_DIR / "eval_report_latest.json").read_text(encoding="utf-8"))

IDS = ["dlg-0362", "dlg-0468", "dlg-0325", "syn-007", "dlg-0493", "dlg-0514"]
before_map = {x["case_id"]: x for x in BEFORE["results"]}
after_map = {x["case_id"]: x for x in AFTER["results"]}

CLARIFY_KW = [
    "澄清",
    "确认一下",
    "你更想",
    "想换成哪",
    "具体哪",
    "哪一类",
    "可以说",
    "方便说",
    "更具体",
    "哪家",
    "哪个",
    "需要你确认",
    "先确认",
    "帮我确认",
    "再确认",
    "不确定你",
    "你指的是",
    "想替换成",
    "想换成什么",
]


def severity(item: dict) -> tuple[str, list[str]]:
    codes: list[str] = []
    for f in item.get("failures") or []:
        codes.extend(f.get("failure_codes") or [])
    codes = list(dict.fromkeys(codes))
    if not item.get("passed"):
        return "hard", codes
    if codes:
        return "soft", codes
    return "ok", codes


def label(sev: str, codes: list[str], overall: float) -> str:
    c = ",".join(codes) if codes else "—"
    if sev == "hard":
        return f"硬失败 `{c}` · {overall:.2f}"
    if sev == "soft":
        return f"软失败 `{c}` · {overall:.2f}"
    return f"通过 `{c}` · {overall:.2f}"


def verdict(b_sev: str, a_sev: str) -> str:
    if a_sev == "ok" and b_sev in ("hard", "soft"):
        return "已改善"
    if a_sev == "hard" and b_sev == "hard":
        return "仍硬失败"
    if a_sev == "soft" and b_sev == "soft":
        return "仍软失败"
    if a_sev == "soft" and b_sev == "hard":
        return "改善为软失败"
    if a_sev == "hard" and b_sev != "hard":
        return "回退"
    if a_sev == "ok" and b_sev == "ok":
        return "仍通过"
    return "有变化"


rows = []
for cid in IDS:
    b = before_map[cid]
    a = after_map[cid]
    bs, bc = severity(b)
    as_, ac = severity(a)
    turns = []
    for t in a.get("turn_trace") or []:
        asst = (t.get("assistant") or "").replace("\n", " ").strip()
        hits = [kw for kw in CLARIFY_KW if kw in asst]
        turns.append(
            {
                "turn": t.get("turn"),
                "user": t.get("user") or "",
                "assistant": asst,
                "stops": t.get("stops") or [],
                "passed": t.get("passed"),
                "overall": t.get("overall"),
                "failure_codes": t.get("failure_codes") or [],
                "clarify_kw": hits,
                "want_places": t.get("want_places") or [],
                "scene": t.get("scene") or "",
                "mode": t.get("mode") or "",
                "metrics": t.get("metrics") or {},
            }
        )
    rows.append(
        {
            "case_id": cid,
            "city": a.get("city"),
            "style_scene": a.get("style_scene"),
            "before": {
                "sev": bs,
                "label": label(bs, bc, float(b["overall"])),
                "passed": b.get("passed"),
                "overall": b.get("overall"),
                "codes": bc,
                "stops": (b.get("plan_summary") or {}).get("stops") or [],
            },
            "after": {
                "sev": as_,
                "label": label(as_, ac, float(a["overall"])),
                "passed": a.get("passed"),
                "overall": a.get("overall"),
                "codes": ac,
                "stops": (a.get("plan_summary") or {}).get("stops") or [],
            },
            "verdict": verdict(bs, as_),
            "turns": turns,
        }
    )

out_path = OUT_DIR / "_quality_extract.json"
out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

# Markdown summary for parent
md: list[str] = []
md.append("# 质量修复后 Badcase 复测（6 条）")
md.append("")
md.append(f"- **命令**：`python -m tripsense.eval --multi-turn --llm --judge stub --ids {','.join(IDS)} --out data/eval/reports/badcase_rerun_quality_20260926`")
md.append("- **对照 before**：`data/eval/reports/badcase_rerun_20260926/`")
md.append("- **本轮 after**：`data/eval/reports/badcase_rerun_quality_20260926/`")
md.append(f"- **汇总**：{AFTER['passed']}/{AFTER['total']} Pass（pass_rate={AFTER['pass_rate']}）")
md.append("")
md.append("## 1. 对照表")
md.append("")
md.append("| case_id | before | after | verdict |")
md.append("|---------|--------|-------|---------|")
for r in rows:
    md.append(f"| `{r['case_id']}` | {r['before']['label']} | {r['after']['label']} | {r['verdict']} |")
md.append("")
md.append("## 2. 逐案逐轮")
md.append("")

for r in rows:
    md.append(f"### `{r['case_id']}` · {r['style_scene']} · {r['city']}")
    md.append("")
    md.append(
        f"- **Case Pass / overall / codes**：{r['before']['label']} → {r['after']['label']}（{r['verdict']}）"
    )
    md.append(f"- **终局站**：{' → '.join(r['after']['stops']) if r['after']['stops'] else '—'}")
    md.append("")
    for t in r["turns"]:
        stops = " → ".join(t["stops"]) if t["stops"] else "—"
        codes = ",".join(t["failure_codes"]) if t["failure_codes"] else "—"
        pass_zh = "过" if t["passed"] else "挂"
        md.append(f"#### T{t['turn']}")
        md.append("")
        md.append(f"- **用户**：{t['user']}")
        md.append(f"- **助手（摘）**：{t['assistant'][:200]}{'…' if len(t['assistant']) > 200 else ''}")
        md.append(f"- **当轮路线**：{stops}")
        md.append(f"- **当轮**：{pass_zh} · overall {t['overall']}；codes `{codes}`")
        if t["clarify_kw"]:
            md.append(f"- **澄清关键词**：{', '.join(t['clarify_kw'])}")
        md.append("")

# syn-007 T2 clarify note
syn = next(r for r in rows if r["case_id"] == "syn-007")
t2 = next((t for t in syn["turns"] if t["turn"] == 2), None)
md.append("## 3. syn-007 T2 澄清是否触发")
md.append("")
if t2:
    md.append(f"- **用户**：{t2['user']}")
    md.append(f"- **助手**：{t2['assistant'][:300]}")
    md.append(f"- **当轮路线**：{' → '.join(t2['stops']) if t2['stops'] else '—'}")
    md.append(
        f"- **澄清关键词命中**：{', '.join(t2['clarify_kw']) if t2['clarify_kw'] else '无'}"
    )
    fired = bool(t2["clarify_kw"]) or ("确认" in t2["assistant"]) or ("哪" in t2["assistant"] and "换" in t2["user"])
    md.append(f"- **判定**：{'疑似已触发澄清' if t2['clarify_kw'] else '未从回复中检出明确澄清话术（需结合产品 clarify 标志再判）'}")
md.append("")
md.append("## 4. 报告路径")
md.append("")
md.append("- after 目录：`data/eval/reports/badcase_rerun_quality_20260926/`")
md.append("- `eval_report_latest.json`")
md.append("- `turn_traces_latest.md`（若生成）")
md.append("- `_quality_extract.json`（本脚本输出）")
md.append("- before：`data/eval/reports/badcase_rerun_20260926/`")

md_path = OUT_DIR / "QUALITY_RERUN_SUMMARY_ZH.md"
md_path.write_text("\n".join(md), encoding="utf-8")
print(f"wrote {out_path}")
print(f"wrote {md_path}")
for r in rows:
    print(r["case_id"], r["verdict"], r["before"]["label"], "=>", r["after"]["label"])
if t2:
    print("syn-007 T2 clarify_kw:", t2["clarify_kw"])
    print("syn-007 T2 assistant:", t2["assistant"][:250])
    print("syn-007 T2 stops:", t2["stops"])
