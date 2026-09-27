# -*- coding: utf-8 -*-
"""Extract before/after + turn traces for Chinese summary."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPORT_DIR = Path(__file__).resolve().parent
AFTER = json.loads((REPORT_DIR / "eval_report_latest.json").read_text(encoding="utf-8"))
BEFORE = json.loads((ROOT / "data/eval/reports/eval_report_llm_full.json").read_text(encoding="utf-8"))
before_map = {x["case_id"]: x for x in BEFORE["results"]}

ORDER = [
    "dlg-0240",
    "dlg-0362",
    "dlg-0450",
    "dlg-0468",
    "dlg-0120",
    "dlg-0325",
    "dlg-0367",
    "dlg-0493",
    "dlg-0514",
    "dlg-0516",
    "dlg-0724",
    "syn-002",
    "syn-007",
]


def severity(item: dict) -> str:
    codes: list[str] = []
    for f in item.get("failures") or []:
        codes.extend(f.get("failure_codes") or [])
    codes = list(dict.fromkeys(codes))
    if not item.get("passed"):
        return "hard", codes
    if codes:
        return "soft", codes
    return "ok", codes


def short(text: str, n: int = 120) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def metric_line(m: dict, key: str) -> str:
    row = (m or {}).get(key) or {}
    detail = row.get("detail")
    if detail:
        return str(detail)
    if "score" in row:
        return f"score={row.get('score')} passed={row.get('passed')}"
    return "—"


out: list[dict] = []
by_id = {x["case_id"]: x for x in AFTER["results"]}
for cid in ORDER:
    item = by_id[cid]
    b = before_map.get(cid, {})
    b_sev, b_codes = severity(b) if b else ("?", [])
    a_sev, a_codes = severity(item)
    turns = []
    for t in item.get("turn_trace") or []:
        m = t.get("metrics") or {}
        turns.append(
            {
                "turn": t.get("turn"),
                "user": t.get("user") or "",
                "assistant": short(t.get("assistant") or "", 160),
                "stops": t.get("stops") or [],
                "passed": t.get("passed"),
                "overall": t.get("overall"),
                "failure_codes": t.get("failure_codes") or [],
                "scene": t.get("scene") or "",
                "mode": t.get("mode") or "",
                "want_places": t.get("want_places") or [],
                "theme": metric_line(m, "theme"),
                "geo": metric_line(m, "geo"),
                "density": metric_line(m, "density"),
                "ccp": metric_line(m, "ccp"),
            }
        )
    if a_sev == "ok" and b_sev in ("hard", "soft"):
        verdict = "improved"
    elif a_sev == "hard" and b_sev != "hard":
        verdict = "regressed"
    elif a_sev == b_sev == "ok":
        verdict = "still_ok"
    elif a_sev == "hard":
        verdict = "still_hard"
    elif a_sev == "soft" and b_sev == "hard":
        verdict = "improved_to_soft"
    elif a_sev == "soft":
        verdict = "still_soft"
    else:
        verdict = "changed"
    out.append(
        {
            "case_id": cid,
            "style_scene": item.get("style_scene"),
            "city": item.get("city"),
            "before": {
                "sev": b_sev,
                "passed": b.get("passed"),
                "overall": b.get("overall"),
                "codes": b_codes,
                "stops": (b.get("plan_summary") or {}).get("stops") or [],
            },
            "after": {
                "sev": a_sev,
                "passed": item.get("passed"),
                "overall": item.get("overall"),
                "codes": a_codes,
                "stops": (item.get("plan_summary") or {}).get("stops") or [],
            },
            "verdict": verdict,
            "turns": turns,
        }
    )

out_path = REPORT_DIR / "_rerun_extract.json"
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {out_path} n={len(out)}")
for row in out:
    print(
        row["case_id"],
        row["verdict"],
        f"{row['before']['sev']}->{row['after']['sev']}",
        row["before"]["codes"],
        "=>",
        row["after"]["codes"],
    )
