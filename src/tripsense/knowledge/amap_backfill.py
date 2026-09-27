from __future__ import annotations

import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .amap_probe import ProbeTarget, fetch_detail_batch


SERVICE_KEYWORDS = (
    "停车",
    "售票",
    "游客中心",
    "游客服务",
    "服务中心",
    "卫生间",
    "厕所",
    "出入口",
    "入口",
    "出口",
    "充电",
    "客服",
)
TRANSPORT_KEYWORDS = ("地铁", "公交", "车站", "码头", "停车")
ATTRACTION_TYPE_PREFIXES = ("11", "14")
COMMERCIAL_TYPE_PREFIXES = ("05", "06")
SERVICE_TYPE_PREFIXES = ("07", "15", "20")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_child_candidate(child: dict[str, Any]) -> tuple[str, str]:
    """Classify an AMap child as evidence only, never as an accepted site relation."""
    name = str(child.get("name", ""))
    subtype = str(child.get("subtype", ""))
    typecode = str(child.get("typecode", ""))
    searchable = f"{name} {subtype}"

    if any(keyword in searchable for keyword in TRANSPORT_KEYWORDS):
        return "transport", "exclude_non_attraction"
    if any(keyword in searchable for keyword in SERVICE_KEYWORDS):
        return "service", "exclude_non_attraction"
    if typecode.startswith(COMMERCIAL_TYPE_PREFIXES):
        return "commercial", "exclude_non_attraction"
    if typecode.startswith(SERVICE_TYPE_PREFIXES):
        return "service", "exclude_non_attraction"
    if typecode.startswith(ATTRACTION_TYPE_PREFIXES):
        return "attraction_candidate", "review_candidate"
    return "unknown", "review_unknown"


def read_source_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_cache(path: Path) -> dict[str, dict[str, Any]]:
    cached: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return cached
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            poi_id = str(item.get("requested_id", ""))
            if poi_id:
                cached[poi_id] = item
    return cached


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def append_cache(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()


def _advanced_fields(cache_item: dict[str, Any] | None) -> dict[str, Any]:
    poi = cache_item.get("poi") if cache_item else None
    if not isinstance(poi, dict):
        return {}
    business = poi.get("business") if isinstance(poi.get("business"), dict) else {}
    navi = poi.get("navi") if isinstance(poi.get("navi"), dict) else {}
    photos = _dict_list(poi.get("photos"))
    children = _dict_list(poi.get("children"))
    return {
        "amap_detail_updated_at": cache_item.get("fetched_at", ""),
        "amap_name": poi.get("name", ""),
        "amap_address": poi.get("address", ""),
        "amap_location": poi.get("location", ""),
        "amap_typecode": poi.get("typecode", ""),
        "amap_parent_id": poi.get("parent", ""),
        "amap_alias": business.get("alias", ""),
        "amap_business_area": business.get("business_area", ""),
        "amap_opentime_today": business.get("opentime_today", ""),
        "amap_opentime_week": business.get("opentime_week", ""),
        "amap_tel": business.get("tel", poi.get("tel", "")),
        "amap_rating": business.get("rating", ""),
        "amap_cost": business.get("cost", ""),
        "amap_parking_type": business.get("parking_type", ""),
        "amap_photo_count": len(photos),
        "amap_photo_urls": json.dumps(
            [str(item.get("url")) for item in photos if item.get("url")],
            ensure_ascii=False,
        ),
        "amap_photo_titles": json.dumps(
            [str(item.get("title", "")) for item in photos], ensure_ascii=False
        ),
        "amap_child_count": len(children),
        "amap_entr_location": navi.get("entr_location", ""),
        "amap_exit_location": navi.get("exit_location", ""),
        "amap_navi_poiid": navi.get("navi_poiid", ""),
    }


def build_enriched_rows(
    source_rows: list[dict[str, str]], cache: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        poi_id = source.get("poi_id", "")
        item = cache.get(poi_id)
        if poi_id.startswith("ZTRIP_"):
            status = "synthetic_id"
        elif item is None:
            status = "pending"
        elif isinstance(item.get("poi"), dict):
            status = "returned"
        else:
            status = "not_returned"
        row: dict[str, Any] = dict(source)
        row["amap_detail_status"] = status
        row.update(_advanced_fields(item))
        rows.append(row)
    return rows


def build_child_candidates(cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for parent_id, item in cache.items():
        poi = item.get("poi")
        if not isinstance(poi, dict):
            continue
        for child in _dict_list(poi.get("children")):
            child_id = str(child.get("id", ""))
            key = (parent_id, child_id)
            if not child_id or key in seen:
                continue
            seen.add(key)
            role, status = classify_child_candidate(child)
            candidates.append({
                "parent_poi_id": parent_id,
                "parent_name": poi.get("name", ""),
                "child_poi_id": child_id,
                "child_name": child.get("name", ""),
                "child_typecode": child.get("typecode", ""),
                "child_subtype": child.get("subtype", ""),
                "child_address": child.get("address", ""),
                "child_location": child.get("location", ""),
                "candidate_role": role,
                "candidate_status": status,
                "source": "amap_children",
                "fetched_at": item.get("fetched_at", ""),
            })
    return candidates


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_summary(
    source_rows: list[dict[str, str]],
    cache: dict[str, dict[str, Any]],
    child_candidates: list[dict[str, Any]],
    fetched_this_run: int,
) -> dict[str, Any]:
    returned = [item for item in cache.values() if isinstance(item.get("poi"), dict)]
    fields = [_advanced_fields(item) for item in returned]
    child_statuses = Counter(item["candidate_status"] for item in child_candidates)
    return {
        "source_rows": len(source_rows),
        "real_amap_ids": sum(
            bool(row.get("poi_id")) and not row.get("poi_id", "").startswith("ZTRIP_")
            for row in source_rows
        ),
        "cached_ids": len(cache),
        "returned_ids": len(returned),
        "not_returned_ids": len(cache) - len(returned),
        "fetched_this_run": fetched_this_run,
        "with_photos": sum(int(item.get("amap_photo_count", 0)) > 0 for item in fields),
        "with_today_hours": sum(bool(item.get("amap_opentime_today")) for item in fields),
        "with_week_hours": sum(bool(item.get("amap_opentime_week")) for item in fields),
        "with_tel": sum(bool(item.get("amap_tel")) for item in fields),
        "with_rating": sum(bool(item.get("amap_rating")) for item in fields),
        "with_cost": sum(bool(item.get("amap_cost")) for item in fields),
        "with_parent": sum(bool(item.get("amap_parent_id")) for item in fields),
        "with_entrance": sum(bool(item.get("amap_entr_location")) for item in fields),
        "with_exit": sum(bool(item.get("amap_exit_location")) for item in fields),
        "child_candidates": len(child_candidates),
        "child_candidate_statuses": dict(sorted(child_statuses.items())),
    }


def run_backfill(
    input_path: Path,
    cache_path: Path,
    enriched_path: Path,
    children_path: Path,
    summary_path: Path,
    api_key: str,
    limit: int | None = 100,
    batch_size: int = 10,
    interval_seconds: float = 0.3,
    refresh: bool = False,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 10:
        raise ValueError("batch_size must be between 1 and 10")
    source_rows = read_source_rows(input_path)
    cache = read_cache(cache_path)
    targets = [
        ProbeTarget(
            poi_id=row.get("poi_id", ""),
            name=row.get("name", ""),
            district=row.get("district", ""),
            address=row.get("address", ""),
        )
        for row in source_rows
        if row.get("poi_id")
        and not row.get("poi_id", "").startswith("ZTRIP_")
        and (refresh or row.get("poi_id", "") not in cache)
    ]
    if limit is not None:
        targets = targets[:limit]

    fetched_this_run = 0
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        payload = fetch_detail_batch(batch, api_key)
        pois = _dict_list(payload.get("pois"))
        by_id = {str(item.get("id", "")): item for item in pois}
        fetched_at = utc_now_iso()
        records = [
            {
                "requested_id": target.poi_id,
                "requested_name": target.name,
                "fetched_at": fetched_at,
                "poi": by_id.get(target.poi_id),
            }
            for target in batch
        ]
        append_cache(cache_path, records)
        for item in records:
            cache[item["requested_id"]] = item
        fetched_this_run += len(batch)
        if offset + batch_size < len(targets):
            time.sleep(interval_seconds)

    enriched = build_enriched_rows(source_rows, cache)
    child_candidates = build_child_candidates(cache)
    write_csv(enriched_path, enriched)
    write_csv(children_path, child_candidates)
    summary = build_summary(source_rows, cache, child_candidates, fetched_this_run)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
