from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local TripSense MVP web app and API in one process."
    )
    # Bind all interfaces so phones on the same LAN can reach the PC via its
    # 192.168.x.x address. Desktop browsers can still use http://127.0.0.1:8000/.
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 for LAN/phone access; use 127.0.0.1 for localhost-only).",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/tripsense-demo.db")
    parser.add_argument("--reload", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    run_server: Callable[..., Any] | None = None,
) -> None:
    args = build_parser().parse_args(argv)
    database = Path(args.db).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TRIPSENSE_DB_PATH"] = str(database)

    if run_server is None:
        import uvicorn

        run_server = uvicorn.run
    run_server(
        "tripsense.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
