from __future__ import annotations

import argparse
import json
from pathlib import Path

from tripsense.knowledge.amap_probe import DEFAULT_SAMPLE_NAMES, run_probe


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe AMap POI detail fields on a small sample")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--names", nargs="+", default=list(DEFAULT_SAMPLE_NAMES))
    parser.add_argument("--key-env", default="AMAP_WEB_SERVICE_KEY")
    args = parser.parse_args()

    root = args.root.resolve()
    summary = run_probe(
        input_path=root / "data" / "processed" / "poi_shanghai_city_only.csv",
        output_dir=root / "data" / "audit" / "amap_probe",
        names=args.names,
        limit=args.limit,
        key_env=args.key_env,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
