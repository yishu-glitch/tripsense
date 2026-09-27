"""Durable inbox for LLM/Amap-supplemented POIs awaiting catalog import."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data import project_root

_LOCK = threading.Lock()


def pending_supplements_path(data_dir: Path | None = None) -> Path:
    base = data_dir or project_root() / "data"
    return base / "pending" / "poi_supplements.jsonl"


def _norm_key(name: str, city: str) -> tuple[str, str]:
    return ("".join((name or "").strip().lower().split()), (city or "").strip().lower())


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return keys
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys.add(_norm_key(str(row.get("name") or ""), str(row.get("city") or "")))
    return keys


def record_poi_supplement(
    payload: dict[str, Any],
    *,
    data_dir: Path | None = None,
    path: Path | None = None,
) -> bool:
    """Append one supplement row. Idempotent by normalized name+city. Returns True if written."""
    name = str(payload.get("name") or "").strip()
    city = str(payload.get("city") or "").strip()
    if len(name) < 2 or not city:
        return False
    target = path or pending_supplements_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = _norm_key(name, city)
    lng = payload.get("gcj_lng")
    lat = payload.get("gcj_lat")
    try:
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lng = None
    try:
        lat = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        lat = None
    row = {
        "timestamp": payload.get("timestamp")
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "city": city,
        "user_text": str(payload.get("user_text") or "")[:200],
        "want_place": str(payload.get("want_place") or name)[:40],
        "name": name[:40],
        "district": str(payload.get("district") or "")[:20],
        "category": str(payload.get("category") or payload.get("poi_type_en") or "")[:20],
        "category_name": str(payload.get("category_name") or "")[:20],
        "gcj_lng": lng,
        "gcj_lat": lat,
        "poi_id": str(payload.get("poi_id") or ""),
        "source": str(payload.get("source") or "unknown")[:40],
        "geo_quality": str(payload.get("geo_quality") or ("exact" if lng is not None and lat is not None else "unknown"))[
            :20
        ],
        "reason": str(payload.get("reason") or "")[:120],
        "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), list) else [],
    }
    with _LOCK:
        if key in _existing_keys(target):
            return False
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True
