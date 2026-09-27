"""Bounded stay-time model for route construction.

Category defaults were previously 65–90 minutes, which systematically produced
two-stop half-days. Street-scale heritage walks are closer to 35–45 minutes;
museums stay longer. Values are clipped so a single POI cannot consume a
half-day budget unless it is a true destination (ancient town, botanical garden).
"""

from __future__ import annotations

import math
from typing import Any

TYPE_DWELL = {
    "heritage": 48.0,
    "museum": 70.0,
    "park": 50.0,
    "attraction": 45.0,
    "culture": 45.0,
    "leisure": 35.0,
}

SCENE_DWELL = {
    "urban_walk": 40.0,
    "history_architecture": 50.0,
    "museum_exhibition": 70.0,
    "park_nature": 55.0,
    "landmark_sightseeing": 45.0,
    "indoor_leisure": 35.0,
    "family_entertainment": 80.0,
    "commercial_support": 20.0,
}

STREET_MARKERS = ("历史文化名街", "步行街", "路", "坊", "里弄", "巷", "公馆")
LONG_STAY_MARKERS = ("植物园", "度假区", "森林公园", "古镇", "海洋公园", "野生动物园")
HOUSE_MARKERS = ("故居", "旧居", "纪念馆")

PACE_MULTIPLIER = {"slow": 1.12, "fast": 0.88}
MIN_DWELL = 25.0
MAX_DWELL = 95.0


def estimate_dwell(poi: dict[str, Any], *, pace: str = "normal") -> float:
    locked = poi.get("_locked_dwell")
    if locked is not None:
        return float(locked)

    name = str(poi.get("name", ""))
    poi_type = str(poi.get("poi_type_en", poi.get("poi_type", "")))
    scene = str(poi.get("primary_scene", "") or "")

    dwell = SCENE_DWELL.get(scene) or TYPE_DWELL.get(poi_type, 45.0)

    table_mu = poi.get("dwell_mu")
    if table_mu is not None:
        try:
            dwell = float(math.exp(float(table_mu)))
        except (TypeError, ValueError, OverflowError):
            pass

    if any(marker in name for marker in LONG_STAY_MARKERS):
        dwell = max(dwell, 75.0)
    elif any(marker in name for marker in HOUSE_MARKERS):
        dwell = min(dwell, 42.0)
    elif scene == "urban_walk" or any(marker in name for marker in STREET_MARKERS):
        dwell = min(dwell, 40.0)

    dwell *= PACE_MULTIPLIER.get(pace, 1.0)
    return round(min(MAX_DWELL, max(MIN_DWELL, dwell)), 1)
