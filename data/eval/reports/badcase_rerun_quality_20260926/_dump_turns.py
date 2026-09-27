# -*- coding: utf-8 -*-
import json
from pathlib import Path

rows = json.loads(
    Path("data/eval/reports/badcase_rerun_quality_20260926/_quality_extract.json").read_text(
        encoding="utf-8"
    )
)
out = Path("data/eval/reports/badcase_rerun_quality_20260926/_dump_turns.txt")
lines = []
for r in rows:
    lines.append(f"==== {r['case_id']} {r['verdict']} ====")
    lines.append(f"before {r['before']['label']} stops={r['before']['stops']}")
    lines.append(f"after  {r['after']['label']} stops={r['after']['stops']}")
    for t in r["turns"]:
        lines.append(
            f"T{t['turn']} pass={t['passed']} overall={t['overall']:.2f} codes={t['failure_codes']} stops={t['stops']}"
        )
        lines.append(f"  user: {t['user']}")
        lines.append(f"  asst: {t['assistant'][:220]}")
        if t["clarify_kw"]:
            lines.append(f"  CLARIFY: {t['clarify_kw']}")
    lines.append("")
out.write_text("\n".join(lines), encoding="utf-8")
print("wrote", out)
