"""One-shot corpus analysis for dialogue eval selection."""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "dialogue" / "dialogues.jsonl"
OUT = ROOT / "data" / "eval" / "_dialogue_analysis.json"

SCENE_KW = {
    "历史文化游": [
        "历史", "文化", "古迹", "博物馆", "遗址", "胡同", "古建筑", "老建筑",
        "老房子", "老洋房", "石库门", "弄堂", "故居", "风貌", "租界", "民国", "老街",
    ],
    "自然公园游": ["自然", "公园", "爬山", "湿地", "森林", "踏青", "植物园"],
    "城市漫游": ["citywalk", "漫步", "街区", "随便逛", "文艺", "小众", "街巷"],
    "休闲购物游": ["购物", "逛街", "商场", "美食", "咖啡", "餐厅"],
    "亲子研学游": ["孩子", "儿童", "亲子", "带娃", "研学", "科普"],
    "拍照": ["拍照", "摄影", "机位", "出片", "日落", "夜景"],
}
TAG_KW = {
    "add_stop": ["再加", "加上", "加一个", "加个", "还想去", "顺便去", "再去", "插入"],
    "shorten": ["缩短", "压缩", "少去", "时间不够", "赶时间", "太满", "精简", "删掉", "去掉"],
    "replace": ["换成", "换一个", "不要去", "改去", "替换", "别去"],
    "state_change": ["累了", "下雨", "下雨了", "下雨天", "太累", "走不动", "放慢", "加快", "节奏", "体力"],
}
PLACE_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}(?:路|园|馆|寺|庙|塔|桥|湾|广场|公园|博物馆|弄|里|街|巷|滩)"
)
JUNK = ["测试", "test", "asdf", "xxx", "占位", "todo"]


def infer_scene(text: str) -> str:
    scores = {k: sum(w in text for w in v) for k, v in SCENE_KW.items()}
    # photo is a mode overlay; map to scene via product scenes when possible
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "综合观光游"
    if best == "拍照":
        # photo often co-occurs; prefer other scene if tied-ish
        others = {k: v for k, v in scores.items() if k != "拍照"}
        ob = max(others, key=others.get)
        return ob if others[ob] > 0 else "城市漫游"
    return best


def quality_score(row: dict) -> tuple[int, list[str], str, str]:
    dial = row["dialogue"]
    users = [t["user"] for t in dial]
    assts = [t["assistant"] for t in dial]
    text = "\n".join(users)
    score = 0
    reasons: list[str] = []

    if all(len(u) >= 12 for u in users):
        score += 2
    else:
        reasons.append("short_user")
    if all(len(a) >= 12 for a in assts):
        score += 1
    else:
        reasons.append("short_asst")

    place_like = sum(bool(PLACE_RE.search(a)) for a in assts)
    if place_like >= max(1, len(assts) // 2):
        score += 2
    else:
        reasons.append("few_places")

    if len(set(users)) == len(users):
        score += 1
    else:
        reasons.append("dup_users")

    u0 = users[0]
    if any(k in u0 for k in ["计划", "路线", "安排", "想去", "游", "玩", "逛", "拍", "天", "小时", "半天"]):
        score += 1
    else:
        reasons.append("weak_initial")

    mods_set = {t.get("modification_type") for t in dial[1:]}
    if row["n_turns"] >= 3 and ("local" in mods_set or "global" in mods_set):
        score += 1
    elif row["n_turns"] >= 3:
        reasons.append("weak_mod")

    if any(t.get("delta_magnitude") in ("medium", "large") for t in dial[1:]):
        score += 1
    elif row["n_turns"] >= 3:
        reasons.append("flat_delta")

    if any(j in text.lower() for j in JUNK):
        score -= 3
        reasons.append("junk")

    return score, reasons, infer_scene(text), text


def main() -> None:
    rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    scored = []
    for r in rows:
        s, reasons, scene, text = quality_score(r)
        tags = [tag for tag, kws in TAG_KW.items() if any(k in text for k in kws)]
        difficulty = "low"
        if r["n_turns"] >= 4 and len(tags) >= 2:
            difficulty = "high"
        elif r["n_turns"] >= 3 or len(tags) >= 1:
            difficulty = "medium"
        if any(k in text for k in ["亲子", "带娃", "孩子"]) and "半天" in text:
            difficulty = "high" if difficulty != "low" else "medium"
        scored.append(
            {
                "id": r["id"],
                "city": r["city"],
                "scenario": r["scenario"],
                "n_turns": r["n_turns"],
                "persona1": r["persona1"],
                "persona2": r["persona2"],
                "q": s,
                "reasons": reasons,
                "scene": scene,
                "tags": tags,
                "difficulty": difficulty,
                "user0": r["dialogue"][0]["user"][:120],
            }
        )

    scored_s = sorted(scored, key=lambda x: (-x["q"], x["id"]))
    summary = {
        "count": len(rows),
        "cities": dict(collections.Counter(r["city"] for r in rows)),
        "scenarios": dict(collections.Counter(r["scenario"] for r in rows)),
        "n_turns": dict(sorted(collections.Counter(r["n_turns"] for r in rows).items())),
        "quality_hist": dict(sorted(collections.Counter(x["q"] for x in scored).items())),
        "scene": dict(collections.Counter(x["scene"] for x in scored)),
        "difficulty": dict(collections.Counter(x["difficulty"] for x in scored)),
        "thresholds": {str(t): sum(1 for x in scored if x["q"] >= t) for t in range(4, 10)},
        "mod_types": dict(
            collections.Counter(
                t.get("modification_type") for r in rows for t in r["dialogue"]
            )
        ),
        "top10": scored_s[:10],
        "bottom10": scored_s[-10:],
        "scored": scored_s,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k != "scored"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
