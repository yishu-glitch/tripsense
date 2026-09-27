from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tripsense.knowledge.hierarchy import (
    discover_embedded_parent_relations,
    discover_site_relations,
    load_site_definitions,
    merge_relation_candidates,
    write_product_hierarchy,
    write_hierarchy_outputs,
)
from tripsense.knowledge.scope import split_shanghai_scope


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and audit Shanghai site hierarchy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    raw_frame = pd.read_csv(root / "data" / "clean" / "poi_shanghai_clean.csv")
    frame, excluded = split_shanghai_scope(raw_frame)
    frame.to_csv(
        root / "data" / "processed" / "poi_shanghai_city_only.csv",
        index=False,
        encoding="utf-8-sig",
    )
    excluded.to_csv(
        root / "data" / "processed" / "poi_shanghai_nearby_phase2.csv",
        index=False,
        encoding="utf-8-sig",
    )
    definitions = load_site_definitions(
        root / "data" / "knowledge" / "shanghai_site_registry.json"
    )
    curated_candidates = discover_site_relations(frame, definitions)
    discovered_candidates = discover_embedded_parent_relations(frame)
    candidates = merge_relation_candidates(discovered_candidates, curated_candidates)
    summary = write_hierarchy_outputs(
        candidates,
        candidates_path=root / "data" / "processed" / "shanghai_site_candidates.jsonl",
        hierarchy_path=root / "data" / "processed" / "shanghai_site_hierarchy.json",
    )
    summary["scope"] = {
        "raw_rows": len(raw_frame),
        "shanghai_city_rows": len(frame),
        "nearby_phase2_rows": len(excluded),
    }
    summary["product_hierarchy"] = write_product_hierarchy(
        frame,
        candidates,
        poi_path=root / "data" / "processed" / "poi_shanghai_recommendation.csv",
        hierarchy_path=root / "data" / "processed" / "shanghai_site_hierarchy_product.json",
    )
    source_counts = {definition.anchor_poi_id: len(definition.sources) for definition in definitions}
    queue_rows = []
    grouped = {}
    for candidate in candidates:
        key = (candidate.parent_poi_id, candidate.site_name)
        item = grouped.setdefault(
            key,
            {"parent_poi_id": key[0], "site_name": key[1], "accepted": 0, "review": 0, "rejected": 0},
        )
        item[candidate.status] += 1
    for item in grouped.values():
        item["web_source_count"] = source_counts.get(item["parent_poi_id"], 0)
        item["verification_status"] = (
            "source_registered" if item["web_source_count"] else "source_needed"
        )
        item["priority_score"] = item["accepted"] * 2 + item["review"] + item["rejected"]
        queue_rows.append(item)
    queue = pd.DataFrame(queue_rows).sort_values(
        ["verification_status", "priority_score"], ascending=[False, False]
    )
    queue.to_csv(
        root / "data" / "processed" / "shanghai_site_verification_queue.csv",
        index=False,
        encoding="utf-8-sig",
    )
    summary["verification_queue_sites"] = len(queue)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
