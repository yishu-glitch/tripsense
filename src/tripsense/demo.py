from __future__ import annotations

import argparse
import json

from .core.service import TripSenseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=["beijing", "shanghai"], default="beijing")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    plan = TripSenseService().plan(args.text, args.city)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

