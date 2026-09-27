# -*- coding: utf-8 -*-
"""Write Chinese badcase rerun summary for parent/user."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "_rerun_extract.json").read_text(encoding="utf-8"))

VERDICT_ZH = {
    "improved": "已改善（终局过关，原失败码消除）",
    "still_hard": "仍硬失败",
    "still_soft": "仍软失败（overall 过，但有 metric miss）",
    "improved_to_soft": "硬失败降为软失败",
    "regressed": "回退变差",
}

SEV_ZH = {"hard": "硬失败", "soft": "软失败", "ok": "通过", "?": "未知"}


def codes_s(codes: list) -> str:
    return ", ".join(codes) if codes else "—"


def stops_s(stops: list) -> str:
    return " → ".join(stops) if stops else "（空）"


def short_reply(text: str, n: int = 100) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


lines: list[str] = []
lines.append("# LLM Badcase 复测报告（当前代码，排除 dlg-0082）")
lines.append("")
lines.append("- **复测时间**：2026-09-26")
lines.append("- **命令**：`python -m tripsense.eval --multi-turn --llm --judge stub --ids <13 cases> --out data/eval/reports/badcase_rerun_20260926`")
lines.append("- **对照基线**：`data/eval/reports/eval_report_llm_full.json`（LLM 全量 195/200）")
lines.append("- **本轮报告**：`data/eval/reports/badcase_rerun_20260926/`")
lines.append("  - `eval_report_20260926_190823.json` / `eval_report_latest.json`")
lines.append("  - `turn_traces_20260926_190823.md` / `turn_traces_latest.md`")
lines.append("  - `attribution_notes_latest.md`")
lines.append("- **用例范围**：4 条 LLM 新 hard（不含已复测的 dlg-0082）+ 案例评审文档点名的 9 条严重新 soft = **13 条**")
lines.append("- **本轮汇总**：**11/13 Pass**（pass_rate=0.846）；硬失败剩 **2**（dlg-0362、dlg-0468）；软失败剩 **4**（dlg-0325、dlg-0493、dlg-0514、syn-007）")
lines.append("")
lines.append("## 总览对照")
lines.append("")
lines.append("| case_id | 原 LLM | 现 LLM | 一句话 |")
lines.append("|---------|--------|--------|--------|")
for row in data:
    b, a = row["before"], row["after"]
    lines.append(
        f"| `{row['case_id']}` | {SEV_ZH[b['sev']]} `{codes_s(b['codes'])}` · {b['overall']:.2f} "
        f"| {SEV_ZH[a['sev']]} `{codes_s(a['codes'])}` · {a['overall']:.2f} "
        f"| {VERDICT_ZH.get(row['verdict'], row['verdict'])} |"
    )
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 逐案逐轮")
lines.append("")

for row in data:
    cid = row["case_id"]
    b, a = row["before"], row["after"]
    lines.append(f"### `{cid}` · {row['style_scene']} · {row['city']}")
    lines.append("")
    lines.append(
        f"- **Case Pass / overall / codes**：前 `{b['passed']}` / {b['overall']:.3f} / `{codes_s(b['codes'])}`（{SEV_ZH[b['sev']]}）"
        f" → 后 `{a['passed']}` / {a['overall']:.3f} / `{codes_s(a['codes'])}`（{SEV_ZH[a['sev']]}）"
    )
    lines.append(f"- **终局站**：前 `{stops_s(b['stops'])}` → 后 `{stops_s(a['stops'])}`")
    lines.append(f"- **一句话结论**：{VERDICT_ZH.get(row['verdict'], row['verdict'])}")
    lines.append("")
    for t in row["turns"]:
        status = "过" if t["passed"] else "挂"
        extra = f"；{codes_s(t['failure_codes'])}" if t["failure_codes"] else ""
        want = "、".join(t["want_places"]) if t["want_places"] else "—"
        lines.append(f"#### T{t['turn']}")
        lines.append("")
        lines.append(f"- **用户**：{t['user']}")
        lines.append(f"- **助手（摘）**：{short_reply(t['assistant'])}")
        lines.append(f"- **当轮路线**：{stops_s(t['stops'])}")
        lines.append(f"- **场景/节奏**：{t['scene'] or '—'} / {t['mode'] or '—'}")
        lines.append(f"- **当轮**：{status} · overall {float(t['overall'] or 0):.2f}{extra}")
        lines.append(
            f"- **指标**：theme `{t['theme']}`；geo `{t['geo']}`；density `{t['density']}`；ccp `{t['ccp']}`"
        )
        lines.append(f"- **want_places**：{want}")
        lines.append("")
    # one-line why
    why = {
        "dlg-0240": "CCP 超预算硬失败消失；终局北外滩→外滩→K11，planned 在 safe 内。",
        "dlg-0362": "不再塌到常营 mall，但终局变成占位「有北京特色的咖啡馆」，theme=0 且过稀，仍硬失败。",
        "dlg-0450": "主题硬失败消除；终局兴业太古汇→思南路→武康路，购物向 hit 过线。",
        "dlg-0468": "前几轮公园线正常，T5「五道营咖啡馆」落到廊坊会议展览中心，theme=0+过稀，仍硬失败。",
        "dlg-0120": "过稀软失败消除；终局故宫+国博两站，密度可接受。",
        "dlg-0325": "过稀改为能上 798，但先农坛→798 出现 geo_jump（max=151），仍软失败。",
        "dlg-0367": "过稀消除；终局收束浦东博物馆串，主题/密度过关。",
        "dlg-0493": "不再塌常营 mall，五道营+芳草地更贴用户，但 must_scene 休闲购物 hit=0，仍软失败。",
        "dlg-0514": "从单站故宫变为古建馆+自然博物馆，仍 stops=2 expected~5 过稀软失败。",
        "dlg-0516": "终局过关（故宫一带四站）；中途 T3/T4 曾 geo_jump，终局修好。",
        "dlg-0724": "主题软失败消除；终局景山/什刹海一带，不再塌常营 mall。",
        "syn-002": "过稀消除；自然博物馆+静安雕塑公园，半天亲子线过关。",
        "syn-007": "过稀改善但换成「互动体验展馆」占位，出现 geo_jump，仍软失败。",
    }.get(cid, "")
    if why:
        lines.append(f"**判定说明**：{why}")
        lines.append("")
    lines.append("---")
    lines.append("")

lines.append("## 剩余问题优先级（给产品）")
lines.append("")
lines.append("1. **硬失败**：`dlg-0362` / `dlg-0468` — 「咖啡馆/休息」类意图落到占位名或远郊错点（廊坊），需 POI 解析 + 城市内 geo 硬护栏。")
lines.append("2. **软失败 geo_jump**：`dlg-0325` / `syn-007` — 点名远点（798）或占位展馆时跨城跳。")
lines.append("3. **软失败 theme/过稀**：`dlg-0493`（场景标签与实际站不一致）、`dlg-0514`（宽预算仍只 2 站）。")
lines.append("4. **已修好可出账**：硬失败 4→2（0240/0450 绿）；严重 soft 里 0120/0367/0516/0724/syn-002 转绿。")
lines.append("")
lines.append("*dlg-0082 本轮未复跑（此前 want_places 修复后已单独复测通过）。*")
lines.append("")

out = HERE / "BADCASE_RERUN_SUMMARY_ZH.md"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {out} lines={len(lines)}")
