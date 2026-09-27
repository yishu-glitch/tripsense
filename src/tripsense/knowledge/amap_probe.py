from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


AMAP_DETAIL_URL = "https://restapi.amap.com/v5/place/detail"
DEFAULT_SHOW_FIELDS = "business,photos,children,navi"
DEFAULT_SAMPLE_NAMES = (
    "上海动物园",
    "东方明珠广播电视塔",
    "上海博物馆(东馆)",
    "迪士尼小镇",
    "外滩观光隧道",
    "湖心亭(豫园店)",
)


@dataclass(frozen=True)
class ProbeTarget:
    poi_id: str
    name: str
    district: str = ""
    address: str = ""


def select_probe_targets(
    input_path: Path,
    names: Iterable[str] = DEFAULT_SAMPLE_NAMES,
    limit: int = 10,
) -> list[ProbeTarget]:
    """Select unique, real AMap POIs, preferring exact name matches."""
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    selected: list[ProbeTarget] = []
    seen_ids: set[str] = set()
    for requested_name in names:
        exact = [row for row in rows if row.get("name", "").strip() == requested_name]
        candidates = exact or [row for row in rows if requested_name in row.get("name", "")]
        for row in candidates:
            poi_id = row.get("poi_id", "").strip()
            if not poi_id or poi_id.startswith("ZTRIP_") or poi_id in seen_ids:
                continue
            selected.append(
                ProbeTarget(
                    poi_id=poi_id,
                    name=row.get("name", "").strip(),
                    district=row.get("district", "").strip(),
                    address=row.get("address", "").strip(),
                )
            )
            seen_ids.add(poi_id)
            break
        if len(selected) >= limit:
            break
    return selected


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def flatten_detail(target: ProbeTarget, poi: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten fields relevant to TripSense while preserving lists as JSON values."""
    if not poi:
        return {
            "poi_id": target.poi_id,
            "requested_name": target.name,
            "returned": False,
            "returned_name": "",
            "name_consistent": False,
            "source_district": target.district,
            "source_address": target.address,
        }

    business = _as_mapping(poi.get("business"))
    navi = _as_mapping(poi.get("navi"))
    photos = [item for item in _as_list(poi.get("photos")) if isinstance(item, dict)]
    children = [item for item in _as_list(poi.get("children")) if isinstance(item, dict)]
    returned_name = str(poi.get("name", "")).strip()

    return {
        "poi_id": str(poi.get("id") or target.poi_id),
        "requested_name": target.name,
        "returned": True,
        "returned_name": returned_name,
        "name_consistent": bool(returned_name and (
            returned_name == target.name
            or returned_name in target.name
            or target.name in returned_name
        )),
        "source_district": target.district,
        "source_address": target.address,
        "returned_address": poi.get("address", ""),
        "location": poi.get("location", ""),
        "parent_id": poi.get("parent", ""),
        "typecode": poi.get("typecode", ""),
        "business_area": business.get("business_area", ""),
        "opentime_today": business.get("opentime_today", ""),
        "opentime_week": business.get("opentime_week", ""),
        "tel": business.get("tel", poi.get("tel", "")),
        "rating": business.get("rating", ""),
        "cost": business.get("cost", ""),
        "alias": business.get("alias", ""),
        "parking_type": business.get("parking_type", ""),
        "photo_count": len(photos),
        "photo_titles": [str(item.get("title", "")) for item in photos],
        "photo_urls": [str(item.get("url", "")) for item in photos if item.get("url")],
        "child_count": len(children),
        "child_ids": [str(item.get("id", "")) for item in children if item.get("id")],
        "child_names": [str(item.get("name", "")) for item in children if item.get("name")],
        "entr_location": navi.get("entr_location", ""),
        "exit_location": navi.get("exit_location", ""),
        "navi_poiid": navi.get("navi_poiid", ""),
    }


def fetch_detail_batch(
    targets: list[ProbeTarget],
    api_key: str,
    timeout: float = 15.0,
    retries: int = 2,
) -> dict[str, Any]:
    if not 1 <= len(targets) <= 10:
        raise ValueError("AMap detail requests require 1 to 10 POI IDs")
    params = {
        "id": "|".join(target.poi_id for target in targets),
        "show_fields": DEFAULT_SHOW_FIELDS,
        "key": api_key,
    }
    request_url = f"{AMAP_DETAIL_URL}?{urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            with urlopen(request_url, timeout=timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "1":
                raise RuntimeError(
                    "AMap API rejected the request: "
                    f"infocode={payload.get('infocode', '')}, info={payload.get('info', '')}"
                )
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"AMap request failed after {retries + 1} attempts") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def run_probe(
    input_path: Path,
    output_dir: Path,
    names: Iterable[str] = DEFAULT_SAMPLE_NAMES,
    limit: int = 10,
    key_env: str = "AMAP_WEB_SERVICE_KEY",
) -> dict[str, Any]:
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Environment variable {key_env} is not set")

    targets = select_probe_targets(input_path, names=names, limit=limit)
    if not targets:
        raise RuntimeError("No valid AMap POI IDs were selected")

    payload = fetch_detail_batch(targets, api_key)
    pois = [poi for poi in _as_list(payload.get("pois")) if isinstance(poi, dict)]
    by_id = {str(poi.get("id", "")): poi for poi in pois}
    records = [flatten_detail(target, by_id.get(target.poi_id)) for target in targets]

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "amap_detail_probe_raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "amap_detail_probe.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_records = []
    for record in records:
        csv_records.append({
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
            for key, value in record.items()
        })
    with (output_dir / "amap_detail_probe.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_records[0]))
        writer.writeheader()
        writer.writerows(csv_records)

    returned = sum(bool(record["returned"]) for record in records)
    summary = {
        "requested": len(records),
        "returned": returned,
        "name_consistent": sum(bool(record.get("name_consistent")) for record in records),
        "with_photos": sum(int(record.get("photo_count", 0)) > 0 for record in records),
        "with_today_hours": sum(bool(record.get("opentime_today")) for record in records),
        "with_week_hours": sum(bool(record.get("opentime_week")) for record in records),
        "with_parent": sum(bool(record.get("parent_id")) for record in records),
        "with_children": sum(int(record.get("child_count", 0)) > 0 for record in records),
        "with_entrance": sum(bool(record.get("entr_location")) for record in records),
        "with_exit": sum(bool(record.get("exit_location")) for record in records),
    }
    (output_dir / "amap_detail_probe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
