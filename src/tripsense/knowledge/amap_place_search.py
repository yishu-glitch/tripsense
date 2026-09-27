"""Amap place text search for resolving missing named places to real coordinates."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"

CITY_ADCODES = {
    "beijing": "110000",
    "shanghai": "310000",
}

CITY_ZH = {
    "beijing": "北京",
    "shanghai": "上海",
}

FetchJson = Callable[[str, float], dict[str, Any]]


def _default_fetch(url: str, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_place(
    name: str,
    city: str,
    *,
    api_key: str | None = None,
    timeout_seconds: float = 8.0,
    fetch_json: FetchJson | None = None,
) -> dict[str, Any] | None:
    """Return structured geo for the best Amap text hit, or None if unavailable."""
    label = (name or "").strip()
    if len(label) < 2:
        return None
    key = (api_key if api_key is not None else os.getenv("AMAP_WEB_SERVICE_KEY", "")).strip()
    if not key:
        return None
    city_key = "shanghai" if city in {"shanghai", "上海"} else "beijing"
    params = {
        "key": key,
        "keywords": label,
        "city": CITY_ADCODES.get(city_key, CITY_ADCODES["beijing"]),
        "citylimit": "true",
        "offset": 5,
        "page": 1,
        "extensions": "base",
    }
    url = f"{AMAP_PLACE_TEXT_URL}?{urlencode(params)}"
    fetch = fetch_json or _default_fetch
    try:
        payload = fetch(url, timeout_seconds)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    if str(payload.get("status")) != "1":
        return None
    pois = payload.get("pois") or []
    if not isinstance(pois, list) or not pois:
        return None
    best = _pick_best(pois, label)
    if best is None:
        return None
    location = str(best.get("location") or "")
    if "," not in location:
        return None
    lng_s, lat_s = location.split(",", 1)
    try:
        lng, lat = float(lng_s), float(lat_s)
    except ValueError:
        return None
    district = _district_from_amap(best)
    category = _category_from_typecode(str(best.get("typecode") or ""), str(best.get("type") or ""))
    display = str(best.get("name") or label).strip()[:24]
    evidence = []
    address = str(best.get("address") or "").strip()
    if address:
        evidence.append(address[:80])
    type_label = str(best.get("type") or "").strip()
    if type_label:
        evidence.append(type_label[:60])
    return {
        "name": display,
        "city": city_key,
        "district": district,
        "category": category,
        "category_name": _category_name(category),
        "gcj_lng": lng,
        "gcj_lat": lat,
        "source": "amap",
        "reason": f"高德检索：{display}",
        "evidence": evidence,
        "amap_id": str(best.get("id") or ""),
    }


def _pick_best(pois: list[Any], label: str) -> dict[str, Any] | None:
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in pois:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name or not item.get("location"):
            continue
        score = 0.0
        if name == label:
            score = 100.0
        elif label in name:
            score = 80.0
        elif name in label:
            score = 60.0
        else:
            score = 20.0
        # Prefer scenic / park / museum typecodes over shops.
        typecode = str(item.get("typecode") or "")
        if typecode.startswith("11") or typecode.startswith("14"):
            score += 15.0
        if any(marker in name for marker in ("专卖", "商场", "超市", "药店", "研学基地")):
            score -= 40.0
        scored.append((score, item))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _district_from_amap(poi: dict[str, Any]) -> str:
    for key in ("adname", "district"):
        value = str(poi.get(key) or "").strip()
        if value.endswith("区") or value.endswith("县"):
            return value[:20]
    pname = str(poi.get("pname") or "")
    cityname = str(poi.get("cityname") or "")
    # Fallback: nothing reliable
    _ = (pname, cityname)
    return ""


def _category_from_typecode(typecode: str, type_label: str) -> str:
    if typecode.startswith("1101") or "公园" in type_label:
        return "park"
    if typecode.startswith("1401") or "博物馆" in type_label:
        return "museum"
    if typecode.startswith("1102") or any(k in type_label for k in ("风景", "古迹", "文物")):
        return "heritage"
    if "休闲" in type_label or typecode.startswith("08"):
        return "leisure"
    return "attraction"


def _category_name(category: str) -> str:
    return {
        "park": "公园",
        "museum": "博物馆",
        "heritage": "历史街区",
        "leisure": "商业休闲",
        "culture": "文化",
        "attraction": "景点",
    }.get(category, "景点")
