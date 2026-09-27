from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .place_fallback import apply_distance_aliases


SUPPORTED_CITIES = {"beijing", "shanghai"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_city_data(city: str, data_dir: Path | None = None) -> tuple[pd.DataFrame, dict]:
    if city not in SUPPORTED_CITIES:
        raise ValueError(f"unsupported city: {city}")
    base = data_dir or project_root() / "data"
    serving_path = base / "processed" / f"poi_{city}_recommendation.csv"
    poi_path = (
        serving_path
        if city == "shanghai" and serving_path.exists()
        else base / "clean" / f"poi_{city}_clean.csv"
    )
    distance_path = base / "distance" / f"dist_{city}.json"
    pois = pd.read_csv(poi_path, dtype={"poi_id": str})
    with distance_path.open(encoding="utf-8") as stream:
        distances = json.load(stream)
    # Curated mains (e.g. 天坛公园) reuse a fragment already in the all-pairs graph.
    apply_distance_aliases(distances, city)
    return pois, distances


def stable_knowledge_path(city: str, data_dir: Path | None = None) -> Path:
    base = data_dir or project_root() / "data"
    return base / "knowledge" / f"{city}_stable.jsonl"
