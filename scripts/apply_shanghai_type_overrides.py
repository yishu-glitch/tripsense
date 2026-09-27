"""Apply curated POI type/scene corrections onto the Shanghai serving CSV.

The serving table still carries legacy map-category noise (e.g. lakes and
resort zones tagged as heritage). Corrections live in
``data/knowledge/shanghai_type_overrides.json`` so they survive rebuilds when
this script is re-run, and can also be folded into the serving build later.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def apply_overrides(frame: pd.DataFrame, overrides: dict) -> tuple[pd.DataFrame, list[str]]:
    out = frame.copy()
    out["poi_id"] = out["poi_id"].astype(str)
    changed: list[str] = []
    for poi_id, patch in overrides.items():
        mask = out["poi_id"].eq(str(poi_id))
        if not mask.any():
            continue
        for key, value in patch.items():
            if key in {"name", "reason"}:
                continue
            if key == "scene_tags" and isinstance(value, list):
                out.loc[mask, key] = json.dumps(value, ensure_ascii=False)
            elif key in out.columns:
                out.loc[mask, key] = value
        label = patch.get("name") or poi_id
        changed.append(f"{poi_id}:{label}->{patch.get('poi_type_en', '?')}")
    return out, changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    root = args.root
    csv_path = root / "data" / "processed" / "poi_shanghai_recommendation.csv"
    override_path = root / "data" / "knowledge" / "shanghai_type_overrides.json"
    overrides = json.loads(override_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(csv_path, dtype={"poi_id": str})
    updated, changed = apply_overrides(frame, overrides)
    updated.to_csv(csv_path, index=False)
    print(f"updated {len(changed)} rows in {csv_path}")
    for line in changed:
        print(" ", line)


if __name__ == "__main__":
    main()
