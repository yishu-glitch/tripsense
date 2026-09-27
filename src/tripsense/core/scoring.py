"""Scene-aware candidate scoring that does not trust saturated popularity."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import Any

from .models import Intent
from .preference import ATTR_COLUMNS

SCENE_PRIMARY_SCORE = {
    "历史文化游": {
        "history_architecture": 1.00,
        "urban_walk": 0.88,
        "museum_exhibition": 0.72,
        "landmark_sightseeing": 0.55,
        "park_nature": 0.08,
        "indoor_leisure": 0.04,
        "family_entertainment": 0.12,
        "commercial_support": 0.04,
    },
    "城市漫游": {
        "urban_walk": 1.00,
        "history_architecture": 0.70,
        "landmark_sightseeing": 0.62,
        "park_nature": 0.40,
        "museum_exhibition": 0.35,
        "indoor_leisure": 0.10,
        "family_entertainment": 0.15,
        "commercial_support": 0.08,
    },
    "自然公园游": {
        "park_nature": 1.00,
        "landmark_sightseeing": 0.35,
        "urban_walk": 0.25,
        "history_architecture": 0.20,
        "museum_exhibition": 0.15,
        "indoor_leisure": 0.05,
        "family_entertainment": 0.30,
        "commercial_support": 0.05,
    },
    "亲子研学游": {
        "museum_exhibition": 1.00,
        "family_entertainment": 0.92,
        "park_nature": 0.70,
        "landmark_sightseeing": 0.45,
        "history_architecture": 0.40,
        "urban_walk": 0.30,
        "indoor_leisure": 0.20,
        "commercial_support": 0.08,
    },
    "休闲购物游": {
        "indoor_leisure": 1.00,
        "urban_walk": 0.55,
        "landmark_sightseeing": 0.35,
        "commercial_support": 0.40,
        "family_entertainment": 0.30,
        "history_architecture": 0.15,
        "museum_exhibition": 0.12,
        "park_nature": 0.10,
    },
    "综合观光游": {
        "landmark_sightseeing": 1.00,
        "urban_walk": 0.78,
        "history_architecture": 0.70,
        "museum_exhibition": 0.55,
        "park_nature": 0.50,
        "family_entertainment": 0.35,
        "indoor_leisure": 0.12,
        "commercial_support": 0.08,
    },
}

SCENE_PREFERRED_TAGS = {
    "历史文化游": {"architecture", "history", "culture", "citywalk"},
    "城市漫游": {"citywalk", "street_life", "architecture", "photo"},
    "自然公园游": {"nature", "outdoor", "hiking", "low_energy"},
    "亲子研学游": {"family", "education", "museum", "indoor"},
    "休闲购物游": {"shopping", "food", "indoor"},
    "综合观光游": {"landmark", "photo", "citywalk", "history"},
}

MODE_PREFERRED_TAGS = {
    "photo": {"photo", "night", "landmark", "architecture", "waterfront"},
    "deep": {"history", "culture", "architecture", "museum", "education"},
    "relaxed": {"citywalk", "low_energy", "cafe", "street_life"},
    "full": {"landmark", "citywalk", "museum"},
}

TYPE_FALLBACK_SCENE = {
    "heritage": "history_architecture",
    "museum": "museum_exhibition",
    "park": "park_nature",
    "attraction": "landmark_sightseeing",
    "culture": "history_architecture",
    "leisure": "indoor_leisure",
}

FALSE_HERITAGE_MARKERS = (
    "植物园",
    "度假区",
    "森林公园",
    "湿地",
    "滴水湖",
    "美兰湖",
    "海洋公园",
    "野生动物园",
)

OFF_THEME_MUSEUMS = ("证券", "公安", "邮政", "汽车", "航海", "游戏", "乒联")

NIGHTLIFE_NAME_MARKERS = ("酒吧街", "酒吧", "夜店", "清吧", "夜总会", "俱乐部")

LANDMARK_UTTERANCE_CUES = (
    "标志性",
    "经典",
    "打卡",
    "地标",
    "必去",
    "玩一天",
    "一整天",
    "拍照",
    "摄影",
    "出片",
    "多打卡",
    "多跑几个",
    "古建筑",
    "古迹",
)

CORE_DISTRICTS = {
    "shanghai": {"黄浦区", "徐汇区", "静安区", "长宁区", "虹口区"},
    "beijing": {"东城区", "西城区"},
}
INNER_DISTRICTS = {
    "shanghai": {"杨浦区", "普陀区", "浦东新区"},
    "beijing": {"朝阳区", "海淀区", "丰台区", "石景山区"},
}
OUTER_DISTRICTS = {
    "shanghai": {
        "松江区",
        "嘉定区",
        "青浦区",
        "金山区",
        "奉贤区",
        "崇明区",
        "宝山区",
        "闵行区",
    },
    "beijing": {
        "通州区",
        "大兴区",
        "昌平区",
        "房山区",
        "顺义区",
        "怀柔区",
        "密云区",
        "延庆区",
        "门头沟区",
        "平谷区",
    },
}

URBAN_SCENES = {"历史文化游", "城市漫游", "综合观光游"}
HARD_EXCLUDE_SCENES = {
    "历史文化游": {"park_nature", "indoor_leisure", "family_entertainment", "commercial_support"},
    "城市漫游": {"indoor_leisure", "commercial_support"},
    "自然公园游": {"indoor_leisure", "commercial_support"},
    "亲子研学游": {"indoor_leisure", "commercial_support"},
    "综合观光游": {"indoor_leisure", "commercial_support"},
}


def parse_scene_tags(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none"}:
        return set()
    if text.startswith("["):
        try:
            parsed = json.loads(text.replace("'", '"'))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = re.findall(r"[A-Za-z_]+", text)
        return {str(item).strip() for item in parsed if str(item).strip()}
    return {part.strip() for part in text.split(",") if part.strip()}


def rank_normalize(values: Sequence[float]) -> list[float]:
    """Average ranks scaled to ``[0, 1]``. Ties share a rank so saturated popularity collapses."""
    count = len(values)
    if count == 0:
        return []
    if count == 1:
        return [0.5]
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * count
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average = (index + end) / 2.0
        for cursor in range(index, end + 1):
            ranks[ordered[cursor][0]] = average
        index = end + 1
    scale = count - 1
    return [rank / scale for rank in ranks]


def primary_scene_of(row: dict[str, Any]) -> str:
    raw = str(row.get("primary_scene", "") or "").strip()
    if raw and raw.lower() not in {"nan", "none", "uncertain"}:
        return raw
    return TYPE_FALLBACK_SCENE.get(str(row.get("poi_type_en", "")), "")


def scene_affinity(row: dict[str, Any], intent: Intent) -> float:
    table = SCENE_PRIMARY_SCORE.get(intent.scene, SCENE_PRIMARY_SCORE["综合观光游"])
    score = table.get(primary_scene_of(row), 0.35)
    tags = parse_scene_tags(row.get("scene_tags"))
    preferred = SCENE_PREFERRED_TAGS.get(intent.scene, set()) | MODE_PREFERRED_TAGS.get(
        intent.mode, set()
    )
    if preferred:
        overlap = len(tags & preferred)
        score += min(0.12, 0.04 * overlap)
    name = str(row.get("name", ""))
    if intent.scene == "历史文化游" and any(marker in name for marker in FALSE_HERITAGE_MARKERS):
        score = min(score, 0.08)
    if intent.mode == "photo" and {"photo", "night", "waterfront"} & tags:
        score = min(1.0, score + 0.08)
    if intent.start_hour is not None and intent.start_hour >= 16.5:
        if {"night", "waterfront", "photo"} & tags:
            score = min(1.0, score + 0.10)
        if primary_scene_of(row) == "museum_exhibition":
            score = min(score, 0.15)
    if intent.scene == "历史文化游" and "architecture" in tags:
        score = min(1.0, score + 0.06)
    if intent.scene == "历史文化游" and float(row.get("cultural_score", 0) or 0) < 0.3:
        score = min(score, 0.45)
    if intent.scene == "历史文化游" and any(marker in name for marker in OFF_THEME_MUSEUMS):
        score = min(score, 0.25)
    return max(0.0, min(1.0, score))


def lexical_score(row: dict[str, Any], intent: Intent) -> float:
    terms = [term for term in intent.focus_terms if term]
    haystack = " ".join(
        [
            str(row.get("name", "")),
            str(row.get("district", "")),
            str(row.get("category_name", "")),
            " ".join(parse_scene_tags(row.get("scene_tags"))),
            str(row.get("primary_scene", "")),
        ]
    )
    if not terms:
        return 0.35
    hits = sum(1 for term in terms if term and term in str(row.get("name", "")))
    soft = sum(1 for term in terms if term and term in haystack)
    return max(0.0, min(1.0, 0.55 * (hits / len(terms)) + 0.45 * (soft / len(terms))))


def signature_bonus(row: dict[str, Any], intent: Intent) -> float:
    """Nudge well-known architecture streets without breaking geographic clustering.

    Core / local urban-walk places with architecture tags match user mental models
    for "老建筑" (武康路, 思南路). The bonus is small so a dense Huangpu day can
    still win on day 1; multi-day planning then prefers these as later seeds.
    """
    if intent.scene not in {"历史文化游", "城市漫游"}:
        return 0.0
    tags = parse_scene_tags(row.get("scene_tags"))
    if "architecture" not in tags and "history" not in tags:
        return 0.0
    scene = primary_scene_of(row)
    if scene not in {"urban_walk", "history_architecture"}:
        return 0.0
    tier = str(row.get("attraction_tier", "") or "")
    if tier == "core":
        return 0.05
    if tier == "local":
        return 0.03
    return 0.0


def _landmark_hungry(intent: Intent) -> bool:
    text = intent.utterance or ""
    if any(cue in text for cue in LANDMARK_UTTERANCE_CUES):
        return True
    if intent.mode in {"full", "photo"}:
        return True
    if intent.scene in {"综合观光游", "历史文化游", "亲子研学游"} and intent.available_minutes >= 360:
        return True
    return False


def _allows_nightlife(intent: Intent) -> bool:
    text = intent.utterance or ""
    return any(token in text for token in ("酒吧", "夜店", "夜生活", "清吧", "酒吧街"))


def core_landmark_bonus(row: dict[str, Any], intent: Intent) -> float:
    """Prefer city signatures (tier=core / famous mains) for iconic & full-day openers."""
    if not _landmark_hungry(intent):
        return 0.0
    name = str(row.get("name", ""))
    if any(marker in name for marker in NIGHTLIFE_NAME_MARKERS) and not _allows_nightlife(intent):
        return -0.22
    tier = str(row.get("attraction_tier", "") or "").lower()
    main = str(row.get("is_main_site", "")).lower() in {"true", "1"}
    parent = str(row.get("parent_site") or "").strip()
    # Child fragments of a parent site are never "the" landmark stop.
    if parent and not main:
        return -0.06
    scene = primary_scene_of(row)
    try:
        popularity = float(row.get("popularity", 0.0) or 0.0)
    except (TypeError, ValueError):
        popularity = 0.0
    city = str(intent.city or "")
    district = str(row.get("district", ""))
    in_core = district in CORE_DISTRICTS.get(city, set())
    bonus = 0.0
    if tier == "core":
        bonus = 0.14
    elif tier == "major":
        bonus = 0.09
    elif not tier:
        # Beijing (and other cities without tier): popularity + core district + main.
        if main and popularity >= 0.9 and in_core:
            bonus = 0.12
        elif main and popularity >= 0.85:
            bonus = 0.08
        elif scene == "landmark_sightseeing" and popularity >= 0.85 and in_core:
            bonus = 0.07
    if intent.scene == "历史文化游" and scene in {
        "history_architecture",
        "landmark_sightseeing",
        "museum_exhibition",
    }:
        if tier in {"core", "major"} or (main and popularity >= 0.9):
            bonus = max(bonus, 0.11)
    if intent.mode == "photo" and scene in {"landmark_sightseeing", "urban_walk", "park_nature"}:
        if tier in {"core", "major"} or (main and popularity >= 0.88):
            bonus = max(bonus, bonus + 0.03)
    return bonus


def is_theme_signature(row: dict[str, Any], intent: Intent) -> bool:
    return signature_bonus(row, intent) > 0.0 or core_landmark_bonus(row, intent) > 0.05


def district_penalty(row: dict[str, Any], intent: Intent) -> float:
    if intent.scene not in URBAN_SCENES:
        return 0.0
    if any(term in intent.utterance for term in ("古镇", "郊区", "远一点", "周边")):
        return 0.0
    district = str(row.get("district", ""))
    if district in OUTER_DISTRICTS.get(intent.city, set()):
        return 0.28
    if district in INNER_DISTRICTS.get(intent.city, set()):
        return 0.04
    return 0.0


def matches_want_place(row: dict[str, Any], intent: Intent) -> bool:
    places = [place for place in (getattr(intent, "want_places", None) or []) if place]
    if not places:
        return False
    name = str(row.get("name", ""))
    return any(place == name or place in name or name.startswith(place) for place in places)


def is_incompatible(row: dict[str, Any], intent: Intent) -> bool:
    if matches_want_place(row, intent):
        return False
    blocked = set(HARD_EXCLUDE_SCENES.get(intent.scene, set()))
    if "park" in (intent.categories or []):
        blocked.discard("park_nature")
    if primary_scene_of(row) in blocked:
        return True
    if intent.scene == "历史文化游":
        name = str(row.get("name", ""))
        if any(marker in name for marker in FALSE_HERITAGE_MARKERS):
            return True
    return False


def nearness_bonus(row: dict[str, Any], intent: Intent) -> float:
    """Soft geo pull toward a fixed-commitment area (prefer_near)."""
    target = (getattr(intent, "prefer_near", None) or "").strip()
    if len(target) < 2:
        return 0.0
    name = str(row.get("name", ""))
    district = str(row.get("district", ""))
    address = str(row.get("address", ""))
    if target == name or target in name or (len(name) >= 3 and name in target):
        return 0.14
    if target in district or target in address:
        return 0.11
    return 0.0


def want_place_bonus(row: dict[str, Any], intent: Intent) -> float:
    """Pull named sights the user asked for (want_places), not generic themes."""
    places = [place for place in (getattr(intent, "want_places", None) or []) if place]
    if not places:
        return 0.0
    name = str(row.get("name", ""))
    best = 0.0
    for place in places:
        if place == name:
            best = max(best, 0.24)
        elif name.startswith(place) or place in name:
            fragment = "-" in name or "—" in name
            best = max(best, 0.10 if fragment else 0.20)
    return best


def score_records(
    records: Sequence[dict[str, Any]],
    intent: Intent,
    weights: Sequence[float],
) -> list[dict[str, Any]]:
    eligible = [dict(row) for row in records if not is_incompatible(row, intent)]
    if not eligible:
        eligible = [dict(row) for row in records]
    if not eligible:
        return []

    weight_vec = [float(value) for value in weights]
    popularities = [float(row.get("popularity", 0.5) or 0.5) for row in eligible]
    attributes: list[float] = []
    ratings: list[float] = []
    for row in eligible:
        attr = sum(
            float(row.get(name, 0.0) or 0.0) * weight_vec[index]
            for index, name in enumerate(ATTR_COLUMNS)
            if index < len(weight_vec)
        )
        attributes.append(attr)
        raw_rating = row.get("rating")
        try:
            rating = float(raw_rating)
            ratings.append(0.5 if math.isnan(rating) else min(max(rating / 5.0, 0.0), 1.0))
        except (TypeError, ValueError):
            ratings.append(0.5)

    pop_ranks = rank_normalize(popularities)
    attr_ranks = rank_normalize(attributes)
    scored: list[dict[str, Any]] = []
    for index, row in enumerate(eligible):
        scene = scene_affinity(row, intent)
        lexical = lexical_score(row, intent)
        score = (
            0.16 * pop_ranks[index]
            + 0.22 * attr_ranks[index]
            + 0.34 * scene
            + 0.18 * lexical
            + 0.10 * ratings[index]
            + signature_bonus(row, intent)
            + core_landmark_bonus(row, intent)
            + nearness_bonus(row, intent)
            + want_place_bonus(row, intent)
            - district_penalty(row, intent)
        )
        row["_score"] = round(float(score), 4)
        row["_scene_affinity"] = round(scene, 4)
        scored.append(row)
    scored.sort(key=lambda item: (-float(item["_score"]), str(item.get("poi_id", ""))))
    return scored
