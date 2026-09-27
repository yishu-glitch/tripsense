"""Select/edit ~200 high-quality dialogue eval cases from the 800 corpus."""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "dialogue" / "dialogues.jsonl"
OUT_JSONL = ROOT / "data" / "eval" / "dialogue_eval_200.jsonl"
OUT_REPORT = ROOT / "data" / "eval" / "SELECTION_REPORT.md"
ANALYSIS = ROOT / "data" / "eval" / "_dialogue_analysis.json"

SCENE_KW = {
    "历史文化游": [
        "历史", "文化", "古迹", "博物馆", "遗址", "胡同", "古建筑", "老建筑",
        "老房子", "老洋房", "石库门", "弄堂", "故居", "风貌", "租界", "民国", "老街",
    ],
    "自然公园游": ["自然", "公园", "爬山", "湿地", "森林", "踏青", "植物园"],
    "城市漫游": ["citywalk", "漫步", "街区", "随便逛", "文艺", "小众", "街巷", "胡同"],
    "休闲购物游": ["购物", "逛街", "商场", "美食", "咖啡", "餐厅", "小吃"],
    "亲子研学游": ["孩子", "儿童", "亲子", "带娃", "研学", "科普", "孙子", "幼儿"],
}
PERSONA_SCENE = {
    "亲子研学": "亲子研学游",
    "带幼儿家庭": "亲子研学游",
    "历史爱好者": "历史文化游",
    "博物馆爱好者": "历史文化游",
    "建筑控": "历史文化游",
    "Citywalk漫游者": "城市漫游",
    "艺术青年": "城市漫游",
    "咖啡打卡": "休闲购物游",
    "美食探店": "休闲购物游",
    "情侣约会": "城市漫游",
    "独行摄影师": "城市漫游",
}
MODE_KW = {
    "photo": ["拍照", "摄影", "机位", "出片", "日落", "夜景"],
    "relaxed": ["累", "放松", "放空", "慢慢", "不赶", "悠闲", "轻松", "走不了太多"],
    "full": ["充实", "全走", "打卡", "必去", "高效", "特种兵", "多去几个"],
    "deep": ["深度", "文化", "历史", "博物馆", "故事"],
}
TAG_KW = {
    "add_stop": ["再加", "加上", "加一个", "加个", "还想去", "顺便去", "再去", "插入", "多去"],
    "shorten": ["缩短", "压缩", "少去", "时间不够", "赶时间", "太满", "精简", "删掉", "去掉", "半天", "时间紧"],
    "replace": ["换成", "换一个", "不要去", "改去", "替换", "别去", "换掉"],
    "state_change": [
        "累了", "下雨", "下雨了", "下雨天", "太累", "走不动", "放慢", "加快",
        "节奏", "体力", "腿脚", "行动不便",
    ],
}
PLACE_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}(?:路|园|馆|寺|庙|塔|桥|湾|广场|公园|博物馆|弄|里|街|巷|滩|寺|宫|园)"
)

# Target quotas (sum ~200)
SCENE_QUOTA = {
    "历史文化游": 55,
    "城市漫游": 40,
    "自然公园游": 30,
    "休闲购物游": 30,
    "亲子研学游": 30,
    "综合观光游": 15,
}
DIFF_QUOTA = {"high": 60, "medium": 100, "low": 40}
CITY_QUOTA = {"上海": 100, "北京": 100}


def city_key(city: str) -> str:
    return "shanghai" if "上海" in city else "beijing"


def infer_scene(row: dict, text: str) -> str:
    for persona in (row.get("persona1"), row.get("persona2")):
        if persona in PERSONA_SCENE:
            # persona wins for family; otherwise boost
            if PERSONA_SCENE[persona] == "亲子研学游":
                return "亲子研学游"
    scores = {k: sum(w in text for w in v) for k, v in SCENE_KW.items()}
    for persona in (row.get("persona1"), row.get("persona2")):
        if persona in PERSONA_SCENE:
            scores[PERSONA_SCENE[persona]] = scores.get(PERSONA_SCENE[persona], 0) + 2
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "综合观光游"


def infer_modes(text: str, personas: list[str | None]) -> list[str]:
    modes = []
    for mode, kws in MODE_KW.items():
        if any(k in text for k in kws):
            modes.append(mode)
    if any(p and "摄影" in p for p in personas):
        if "photo" not in modes:
            modes.append("photo")
    return modes or ["balanced"]


def infer_tags(text: str) -> list[str]:
    return [tag for tag, kws in TAG_KW.items() if any(k in text for k in kws)]


def infer_difficulty(row: dict, tags: list[str], text: str, scene: str) -> str:
    n = row["n_turns"]
    score = 0
    if n >= 5:
        score += 2
    elif n >= 4:
        score += 1
    score += min(2, len(tags))
    if row.get("scenario") == "drift":
        score += 1
    if row.get("persona2"):
        score += 1
    if scene == "亲子研学游" and ("半天" in text or "4岁" in text or "幼儿" in text):
        score += 2
    if "行动不便" in text or "腿脚" in text:
        score += 1
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def extract_constraints(text: str, scene: str, modes: list[str]) -> dict:
    minutes = 240
    if "半天" in text:
        minutes = 240
    elif "两天" in text or "两日" in text:
        minutes = 420
    elif "三天" in text:
        minutes = 420
    elif "一整天" in text or "一天" in text:
        minutes = 480
    hours = re.search(r"([一二两三四五六七八九十\d]+)\s*个?小时", text)
    if hours:
        cmap = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        raw = hours.group(1)
        h = cmap.get(raw, None)
        if h is None:
            try:
                h = float(raw)
            except ValueError:
                h = None
        if h:
            minutes = int(h * 60)

    constraints = {
        "available_minutes_hint": minutes,
        "must_scene": scene,
        "must_modes": modes,
        "hard": [],
        "soft": [],
    }
    if any(k in text for k in ["腿脚", "行动不便", "走不了太多", "不能走太多"]):
        constraints["hard"].append("low_mobility")
        constraints["soft"].append("prefer_metro_accessible")
    if any(k in text for k in ["孩子", "亲子", "带娃", "幼儿", "孙子"]):
        constraints["hard"].append("family_friendly")
    if "下雨" in text:
        constraints["soft"].append("prefer_indoor_on_rain")
    if "半天" in text:
        constraints["hard"].append("half_day_budget")
    if any(k in text for k in ["老建筑", "武康路", "外滩", "石库门"]):
        constraints["soft"].append("heritage_focus")
    if "拍照" in text or "摄影" in text:
        constraints["soft"].append("photo_worthy")
    return constraints


def quality_flags(row: dict) -> list[str]:
    dial = row["dialogue"]
    users = [t["user"] for t in dial]
    assts = [t["assistant"] for t in dial]
    flags = []
    if any(len(u) < 10 for u in users):
        flags.append("short_user")
    if any(len(a) < 10 for a in assts):
        flags.append("short_asst")
    place_like = sum(bool(PLACE_RE.search(a)) for a in assts)
    if place_like < max(1, len(assts) // 2):
        flags.append("few_places")
    if len(set(users)) < len(users):
        flags.append("dup_users")
    # near-duplicate openings
    u0 = users[0].strip()
    if u0 in ("我想在上海随便走走，感受一下城市氛围，有什么推荐吗？",):
        flags.append("generic_opener")
    return flags


def edit_dialogue(row: dict, flags: list[str], scene: str) -> tuple[dict, list[str]]:
    """Light edits to make borderline cases usable."""
    edits: list[str] = []
    dial = [dict(t) for t in row["dialogue"]]
    if "few_places" in flags or "generic_opener" in flags:
        city = row["city"]
        if city == "上海":
            dial[0]["user"] = (
                "我想在上海做半天城市漫游，从武康路附近开始，走走老洋房和弄堂，"
                "节奏轻松，别安排太满。"
            )
        else:
            dial[0]["user"] = (
                "我想在北京轻松逛半天，重点去有生活气息的胡同和公园，"
                "不要太赶，最好地铁方便。"
            )
        dial[0]["assistant"] = (
            dial[0]["assistant"]
            if len(dial[0]["assistant"]) >= 20
            else "可以先安排步行友好的街区核心段，再留缓冲时间，避免赶场。"
        )
        edits.append("rewrote_generic_opener")
        scene = "城市漫游"
    if "short_asst" in flags:
        for t in dial:
            if len(t["assistant"]) < 10:
                t["assistant"] = "我按你的新约束做了最小调整，并保留已锁定的兴趣点。"
        edits.append("padded_short_assistant")
    new_row = dict(row)
    new_row["dialogue"] = dial
    return new_row, edits


SYNTHETIC_CASES = [
    {
        "id": "syn-001",
        "source_id": None,
        "city": "shanghai",
        "city_zh": "上海",
        "scenario": "single",
        "n_turns": 1,
        "persona1": "建筑控",
        "persona2": None,
        "style_scene": "历史文化游",
        "modes": ["deep"],
        "difficulty": "low",
        "tags": [],
        "expected_constraints": {
            "available_minutes_hint": 240,
            "must_scene": "历史文化游",
            "must_modes": ["deep"],
            "hard": [],
            "soft": ["heritage_focus"],
        },
        "quality_notes": "synthetic: classic offline 「看老建筑」 demo query",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "看老建筑",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            }
        ],
        "ops_sequence": [],
    },
    {
        "id": "syn-002",
        "source_id": None,
        "city": "shanghai",
        "city_zh": "上海",
        "scenario": "mixed",
        "n_turns": 3,
        "persona1": "带幼儿家庭",
        "persona2": None,
        "style_scene": "亲子研学游",
        "modes": ["relaxed"],
        "difficulty": "high",
        "tags": ["shorten", "state_change", "add_stop"],
        "expected_constraints": {
            "available_minutes_hint": 240,
            "must_scene": "亲子研学游",
            "must_modes": ["relaxed"],
            "hard": ["family_friendly", "half_day_budget"],
            "soft": ["prefer_indoor_on_rain"],
        },
        "quality_notes": "synthetic: family half-day sparsity stress case",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "我们一家带4岁小孩，上海玩半天，要轻松亲子路线，别太累。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "下午可能下雨，尽量多安排室内，时间再缩短一点。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
            {
                "turn": 3,
                "user": "能不能再加一个适合小朋友的互动展，但整体还是半天走完。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["state_change", "shorten", "add_stop"],
    },
    {
        "id": "syn-003",
        "source_id": None,
        "city": "shanghai",
        "city_zh": "上海",
        "scenario": "single",
        "n_turns": 2,
        "persona1": "独行摄影师",
        "persona2": None,
        "style_scene": "城市漫游",
        "modes": ["photo"],
        "difficulty": "medium",
        "tags": ["replace", "state_change"],
        "expected_constraints": {
            "available_minutes_hint": 300,
            "must_scene": "城市漫游",
            "must_modes": ["photo"],
            "hard": [],
            "soft": ["photo_worthy", "prefer_indoor_on_rain"],
        },
        "quality_notes": "synthetic: photo + rain replace stop",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "我想在上海拍城市街景，大概五个小时，重点要出片的街道和天际线。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "下雨了，把最后一个室外点换成室内或有檐廊的地方。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["state_change", "replace"],
    },
    {
        "id": "syn-004",
        "source_id": None,
        "city": "beijing",
        "city_zh": "北京",
        "scenario": "mixed",
        "n_turns": 3,
        "persona1": "历史爱好者",
        "persona2": "轻度行动不便",
        "style_scene": "历史文化游",
        "modes": ["deep", "relaxed"],
        "difficulty": "high",
        "tags": ["shorten", "replace", "state_change"],
        "expected_constraints": {
            "available_minutes_hint": 360,
            "must_scene": "历史文化游",
            "must_modes": ["deep", "relaxed"],
            "hard": ["low_mobility"],
            "soft": ["prefer_metro_accessible", "heritage_focus"],
        },
        "quality_notes": "synthetic: mobility + heritage + shorten",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "北京一天历史文化游，想看博物馆和古迹，但我腿脚不便，尽量地铁直达。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "有点累了，把行程缩短，去掉走路多的点。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
            {
                "turn": 3,
                "user": "把其中一个换成室内展馆。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["state_change", "shorten", "replace"],
    },
    {
        "id": "syn-005",
        "source_id": None,
        "city": "beijing",
        "city_zh": "北京",
        "scenario": "drift",
        "n_turns": 4,
        "persona1": "特种兵游客",
        "persona2": "首次到访",
        "style_scene": "综合观光游",
        "modes": ["full"],
        "difficulty": "high",
        "tags": ["add_stop", "shorten", "state_change"],
        "expected_constraints": {
            "available_minutes_hint": 480,
            "must_scene": "综合观光游",
            "must_modes": ["full"],
            "hard": [],
            "soft": [],
        },
        "quality_notes": "synthetic: pace drift full→tired",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "第一次来北京，想一天尽量多打卡经典景点，节奏可以快一点。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "再加一个公园吧。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
            {
                "turn": 3,
                "user": "走累了，节奏放慢，精简到核心几个点。",
                "assistant": "",
                "modification_type": "global",
                "delta_magnitude": "large",
            },
            {
                "turn": 4,
                "user": "时间还是太满，再缩短一小时预算。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["add_stop", "state_change", "shorten"],
    },
    {
        "id": "syn-006",
        "source_id": None,
        "city": "shanghai",
        "city_zh": "上海",
        "scenario": "single",
        "n_turns": 1,
        "persona1": "自然爱好者",
        "persona2": None,
        "style_scene": "自然公园游",
        "modes": ["relaxed"],
        "difficulty": "low",
        "tags": [],
        "expected_constraints": {
            "available_minutes_hint": 300,
            "must_scene": "自然公园游",
            "must_modes": ["relaxed"],
            "hard": [],
            "soft": [],
        },
        "quality_notes": "synthetic: nature park single-turn",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "上海自然公园游，下午慢慢逛植物园和绿地，大概五小时。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            }
        ],
        "ops_sequence": [],
    },
    {
        "id": "syn-007",
        "source_id": None,
        "city": "beijing",
        "city_zh": "北京",
        "scenario": "mixed",
        "n_turns": 2,
        "persona1": "亲子研学",
        "persona2": None,
        "style_scene": "亲子研学游",
        "modes": ["deep"],
        "difficulty": "medium",
        "tags": ["replace"],
        "expected_constraints": {
            "available_minutes_hint": 360,
            "must_scene": "亲子研学游",
            "must_modes": ["deep"],
            "hard": ["family_friendly"],
            "soft": [],
        },
        "quality_notes": "synthetic: family replace stop",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "带10岁孩子在北京做亲子研学，喜欢科普和博物馆，安排大约六小时。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "孩子对那个点没兴趣，换成更互动的展馆。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["replace"],
    },
    {
        "id": "syn-008",
        "source_id": None,
        "city": "shanghai",
        "city_zh": "上海",
        "scenario": "single",
        "n_turns": 2,
        "persona1": "商务顺道",
        "persona2": None,
        "style_scene": "休闲购物游",
        "modes": ["photo", "relaxed"],
        "difficulty": "low",
        "tags": ["shorten"],
        "expected_constraints": {
            "available_minutes_hint": 180,
            "must_scene": "休闲购物游",
            "must_modes": ["photo", "relaxed"],
            "hard": ["half_day_budget"],
            "soft": ["photo_worthy"],
        },
        "quality_notes": "synthetic: half-day coffee/photo",
        "edited": True,
        "edit_notes": ["created_synthetic"],
        "dialogue": [
            {
                "turn": 1,
                "user": "上海出差顺路，只有三小时，想找能喝咖啡拍照的地方。",
                "assistant": "",
                "modification_type": "initial",
                "delta_magnitude": "small",
            },
            {
                "turn": 2,
                "user": "再压缩一点，别排太满。",
                "assistant": "",
                "modification_type": "local",
                "delta_magnitude": "medium",
            },
        ],
        "ops_sequence": ["shorten"],
    },
]


def build_case(row: dict, edited_row: dict, edits: list[str], flags: list[str]) -> dict:
    text = "\n".join(t["user"] for t in edited_row["dialogue"])
    scene = infer_scene(edited_row, text)
    modes = infer_modes(text, [edited_row.get("persona1"), edited_row.get("persona2")])
    tags = infer_tags(text)
    difficulty = infer_difficulty(edited_row, tags, text, scene)
    constraints = extract_constraints(text, scene, modes)
    ops = []
    for t in edited_row["dialogue"][1:]:
        u = t["user"]
        for tag, kws in TAG_KW.items():
            if any(k in u for k in kws) and tag not in ops:
                ops.append(tag)
    notes = []
    if flags:
        notes.append("flags:" + ",".join(flags))
    if edits:
        notes.append("edited:" + ",".join(edits))
    if not notes:
        notes.append("corpus_keep")
    return {
        "id": f"dlg-{edited_row['id']:04d}",
        "source_id": edited_row["id"],
        "city": city_key(edited_row["city"]),
        "city_zh": edited_row["city"],
        "scenario": edited_row["scenario"],
        "n_turns": edited_row["n_turns"],
        "persona1": edited_row.get("persona1"),
        "persona2": edited_row.get("persona2"),
        "style_scene": scene,
        "modes": modes,
        "difficulty": difficulty,
        "tags": tags,
        "expected_constraints": constraints,
        "quality_notes": "; ".join(notes),
        "edited": bool(edits),
        "edit_notes": edits,
        "dialogue": edited_row["dialogue"],
        "ops_sequence": ops,
    }


def stratified_select(candidates: list[dict], n: int = 192) -> list[dict]:
    """Greedy stratified selection to hit scene/diff/city/tag quotas."""
    selected: list[dict] = []
    used_source: set[int] = set()
    scene_count = collections.Counter()
    diff_count = collections.Counter()
    city_count = collections.Counter()
    tag_need = {"add_stop": 35, "shorten": 40, "replace": 35, "state_change": 45}
    tag_count = collections.Counter()

    def need_score(c: dict) -> float:
        s = 0.0
        sc = c["style_scene"]
        if scene_count[sc] < SCENE_QUOTA.get(sc, 10):
            s += 3 + (SCENE_QUOTA.get(sc, 10) - scene_count[sc]) * 0.1
        else:
            s -= 2
        if diff_count[c["difficulty"]] < DIFF_QUOTA.get(c["difficulty"], 20):
            s += 2
        else:
            s -= 1
        if city_count[c["city_zh"]] < CITY_QUOTA.get(c["city_zh"], 80):
            s += 1.5
        for t in c["tags"]:
            if tag_count[t] < tag_need.get(t, 20):
                s += 1.2
        # prefer richer ops coverage and non-dup openings
        s += 0.3 * len(c["tags"])
        s += 0.2 * c["n_turns"]
        if c.get("edited"):
            s -= 0.5
        if "generic_opener" in c.get("quality_notes", ""):
            s -= 1
        return s

    # sort by need iteratively
    pool = list(candidates)
    while len(selected) < n and pool:
        pool.sort(key=need_score, reverse=True)
        pick = pool.pop(0)
        if pick["source_id"] in used_source:
            continue
        # soft reject over-quota scenes once we have alternatives
        if (
            scene_count[pick["style_scene"]] >= SCENE_QUOTA.get(pick["style_scene"], 99) + 5
            and len(pool) > n - len(selected)
        ):
            continue
        selected.append(pick)
        used_source.add(pick["source_id"])
        scene_count[pick["style_scene"]] += 1
        diff_count[pick["difficulty"]] += 1
        city_count[pick["city_zh"]] += 1
        for t in pick["tags"]:
            tag_count[t] += 1

    return selected


def main() -> None:
    raw = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    dropped = []
    candidates = []
    edited_count = 0

    # Dedup near-identical first user turns (keep first occurrence only as candidate)
    seen_openers: dict[str, int] = {}
    for row in raw:
        flags = quality_flags(row)
        opener = row["dialogue"][0]["user"].strip()
        if opener in seen_openers and "generic_opener" not in flags:
            # allow but mark duplicate family
            if seen_openers[opener] >= 2 and not any(
                k in opener for k in ["亲子", "孩子", "老建筑", "半天"]
            ):
                dropped.append(
                    {
                        "id": row["id"],
                        "reason": "duplicate_opener_cap",
                        "opener": opener[:60],
                    }
                )
                continue
            seen_openers[opener] += 1
        else:
            seen_openers[opener] = seen_openers.get(opener, 0) + 1

        if "few_places" in flags and "generic_opener" not in flags and len(flags) >= 2:
            dropped.append({"id": row["id"], "reason": "low_quality:" + ",".join(flags)})
            continue

        edited_row, edits = edit_dialogue(row, flags, infer_scene(row, opener))
        if edits:
            edited_count += 1
        case = build_case(row, edited_row, edits, flags)
        # drop still-weak after edit
        if "few_places" in flags and not edits and case["style_scene"] == "综合观光游":
            dropped.append({"id": row["id"], "reason": "unrecoverable_sparse"})
            continue
        candidates.append(case)

    selected = stratified_select(candidates, n=192)
    # append synthetics to reach 200 and fill gaps
    final = selected + SYNTHETIC_CASES
    # if somehow over, trim lowest-need from selected only
    if len(final) > 200:
        final = selected[: 200 - len(SYNTHETIC_CASES)] + SYNTHETIC_CASES
    while len(final) < 200:
        # pull more from candidates
        have = {c["id"] for c in final}
        extras = [c for c in candidates if c["id"] not in have]
        if not extras:
            break
        final.append(extras[0])

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for case in final:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    scene_c = collections.Counter(c["style_scene"] for c in final)
    diff_c = collections.Counter(c["difficulty"] for c in final)
    city_c = collections.Counter(c["city"] for c in final)
    tag_c = collections.Counter(t for c in final for t in c["tags"])
    turn_c = collections.Counter(c["n_turns"] for c in final)
    edited_in_final = sum(1 for c in final if c.get("edited"))
    synthetic = sum(1 for c in final if str(c["id"]).startswith("syn-"))

    report = f"""# Dialogue Eval-200 Selection Report

## Source
- Path: `data/dialogue/dialogues.jsonl`
- Corpus size: **{len(raw)}**
- Cities: 上海 {sum(1 for r in raw if r['city']=='上海')}, 北京 {sum(1 for r in raw if r['city']=='北京')}
- Scenarios: {dict(collections.Counter(r['scenario'] for r in raw))}
- Turns: {dict(sorted(collections.Counter(r['n_turns'] for r in raw).items()))}

## Filtering
- Candidate pool after quality/dedup filters: **{len(candidates)}**
- Dropped: **{len(dropped)}**
  - Top drop reasons: {dict(collections.Counter(d['reason'].split(':')[0] for d in dropped).most_common(8))}
- Light-edited corpus cases in pool: **{edited_count}**
- Final set size: **{len(final)}**
  - From corpus: **{len(final) - synthetic}**
  - Synthetic fillers: **{synthetic}**
  - Edited (incl. synthetic): **{edited_in_final}**

## Why drops happened
1. Near-duplicate openers capped (synthetic corpus repeats similar first turns).
2. Sparse assistant place mentions + weak openers without recoverable signal.
3. Preference for stratified coverage over dumping all q=9 clones.

## Final distribution
- style_scene: {dict(scene_c)}
- difficulty: {dict(diff_c)}
- city: {dict(city_c)}
- tags (multi-label): {dict(tag_c)}
- n_turns: {dict(sorted(turn_c.items()))}

## Synthetic cases (gap fillers)
| id | purpose |
|----|---------|
| syn-001 | Offline 「看老建筑」 demo |
| syn-002 | Family half-day + rain + add_stop (known sparse gap) |
| syn-003 | Photo citywalk + rain replace |
| syn-004 | Heritage + mobility + shorten/replace |
| syn-005 | Pace drift full→tired + shorten |
| syn-006 | Nature park single-turn |
| syn-007 | Family replace stop (Beijing) |
| syn-008 | Half-day coffee/photo shop |

## Output
- `{OUT_JSONL.relative_to(ROOT).as_posix()}`
"""
    OUT_REPORT.write_text(report, encoding="utf-8")
    print(report)
    print("dropped_sample", dropped[:8])


if __name__ == "__main__":
    main()
