from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tripsense.knowledge.amap_backfill import run_backfill


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Shanghai POI details from AMap with checkpoint/resume"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.3)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--key-env", default="AMAP_WEB_SERVICE_KEY")
    args = parser.parse_args()

    api_key = os.environ.get(args.key_env, "").strip()
    if not api_key:
        parser.error(f"environment variable {args.key_env} is not set")

    root = args.root.resolve()
    summary = run_backfill(
        input_path=root / "data" / "processed" / "poi_shanghai_city_only.csv",
        cache_path=root / "data" / "source" / "amap_detail_backfill.jsonl",
        enriched_path=root / "data" / "processed" / "poi_shanghai_amap_enriched.csv",
        children_path=root / "data" / "processed" / "amap_child_candidates.csv",
        summary_path=root / "data" / "audit" / "amap_backfill_summary.json",
        api_key=api_key,
        limit=None if args.all else args.limit,
        batch_size=args.batch_size,
        interval_seconds=args.interval,
        refresh=args.refresh,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
