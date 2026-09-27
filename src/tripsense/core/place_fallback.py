"""Named-place fallback when the local catalog cannot resolve a user mention.

Keeps unknown fields empty (no invented hours / tickets / Amap ids). Travel
hops reuse an existing graph node so we do not rebuild all-pairs distances.
"""

from __future__ import annotations

from typing import Any

# Curated mains that share a fragment already in dist_{city}.json.
# 天坛公园 (ZTRIP_BJ_007) aliases 天坛公园-祈年殿 so hops stay in the 东城 cluster.
DISTANCE_ALIASES = {
    "beijing": {
        "ZTRIP_BJ_007": "B000A843UD",
    }
}

CITY_PLACE_PRIORS: dict[str, dict[str, dict[str, Any]]] = {
    "beijing": {
        "天坛": {
            "name": "天坛公园",
            "district": "东城区",
            "poi_type_en": "park",
            "category_name": "公园",
            "gcj_lng": 116.4108,
            "gcj_lat": 39.8822,
            "reason": "天安门以南的明清祭天园林",
        },
        "天坛公园": {
            "name": "天坛公园",
            "district": "东城区",
            "poi_type_en": "park",
            "category_name": "公园",
            "gcj_lng": 116.4108,
            "gcj_lat": 39.8822,
            "reason": "天安门以南的明清祭天园林",
        },
    }
}

CITY_PINS = {
    "beijing": {
        "district": "东城区",
        "gcj_lng": 116.3974,
        "gcj_lat": 39.9037,
        "poi_type_en": "attraction",
        "category_name": "景点",
    },
    "shanghai": {
        "district": "黄浦区",
        "gcj_lng": 121.490,
        "gcj_lat": 31.239,
        "poi_type_en": "attraction",
        "category_name": "景点",
    },
}

# District centroids for soft-pin when public search has no exact lat/lng.
DISTRICT_CENTROIDS: dict[str, dict[str, tuple[float, float]]] = {
    "beijing": {
        "东城区": (116.4164, 39.9289),
        "西城区": (116.3668, 39.9129),
        "朝阳区": (116.4864, 39.9215),
        "海淀区": (116.2981, 39.9599),
        "丰台区": (116.2871, 39.8585),
        "石景山区": (116.2230, 39.9060),
        "通州区": (116.6586, 39.9097),
        "昌平区": (116.2312, 40.2207),
        "大兴区": (116.3414, 39.7269),
        "顺义区": (116.6546, 40.1301),
        "房山区": (116.1433, 39.7485),
        "门头沟区": (116.1020, 39.9404),
        "平谷区": (117.1213, 40.1406),
        "怀柔区": (116.6319, 40.3160),
        "密云区": (116.8432, 40.3769),
        "延庆区": (115.9748, 40.4568),
    },
    "shanghai": {
        "黄浦区": (121.490, 31.231),
        "徐汇区": (121.4365, 31.1883),
        "静安区": (121.4477, 31.2288),
        "长宁区": (121.4222, 31.2181),
        "虹口区": (121.5051, 31.2646),
        "杨浦区": (121.5260, 31.2595),
        "普陀区": (121.3955, 31.2496),
        "浦东新区": (121.5447, 31.2215),
        "闵行区": (121.3816, 31.1128),
        "宝山区": (121.4891, 31.4054),
        "嘉定区": (121.2653, 31.3756),
        "松江区": (121.2277, 31.0322),
        "青浦区": (121.1242, 31.1509),
        "金山区": (121.3419, 30.7418),
        "奉贤区": (121.4740, 30.9179),
        "崇明区": (121.3974, 31.6239),
    },
}

# CCP travel inflation when pin is approximate (exact → 1.0).
GEO_UNCERTAINTY = {
    "exact": 1.0,
    "district": 1.3,
    "anchor": 1.55,
    "city_prior": 1.9,
}

ALLOWED_SYNTHETIC_TYPES = {
    "attraction",
    "heritage",
    "park",
    "museum",
    "culture",
    "leisure",
}

CITY_BOUNDS = {
    "beijing": (115.7, 117.5, 39.4, 40.6),
    "shanghai": (120.8, 122.2, 30.7, 31.9),
}


def lookup_place_prior(name: str, city: str) -> dict[str, Any] | None:
    label = (name or "").strip()
    if not label:
        return None
    city_priors = CITY_PLACE_PRIORS.get(city) or {}
    if label in city_priors:
        return dict(city_priors[label])
    for key, prior in city_priors.items():
        if label.startswith(key) or key.startswith(label) or key in label:
            return dict(prior)
    return None


def city_pin(city: str) -> dict[str, Any]:
    return dict(CITY_PINS.get(city) or CITY_PINS["beijing"])


def district_centroid(city: str, district: str) -> tuple[float, float] | None:
    label = (district or "").strip()
    if not label:
        return None
    city_map = DISTRICT_CENTROIDS.get(city) or {}
    if label in city_map:
        return city_map[label]
    for key, coords in city_map.items():
        if label in key or key in label:
            return coords
    return None


def geo_uncertainty_factor(quality: str) -> float:
    return float(GEO_UNCERTAINTY.get(quality) or GEO_UNCERTAINTY["city_prior"])


def synthetic_poi_id(name: str) -> str:
    label = "".join((name or "").strip().split())[:16]
    return f"ext:{label or 'place'}"


def build_synthetic_poi(
    name: str,
    city: str,
    *,
    district: str = "",
    gcj_lng: float | None = None,
    gcj_lat: float | None = None,
    poi_type_en: str = "attraction",
    category_name: str = "",
    reason: str = "",
    travel_alias: str | None = None,
    geo_quality: str = "exact",
) -> dict[str, Any]:
    pin = city_pin(city)
    poi_type = poi_type_en if poi_type_en in ALLOWED_SYNTHETIC_TYPES else "attraction"
    lng = float(gcj_lng if gcj_lng is not None else pin["gcj_lng"])
    lat = float(gcj_lat if gcj_lat is not None else pin["gcj_lat"])
    lng, lat = _clamp_city_coords(city, lng, lat)
    quality = geo_quality if geo_quality in GEO_UNCERTAINTY else "city_prior"
    display = (name or "").strip()[:24] or "未命名地点"
    return {
        "poi_id": synthetic_poi_id(display),
        "name": display,
        "city": "北京" if city == "beijing" else "上海",
        "category_name": category_name or pin["category_name"],
        "poi_type_en": poi_type,
        "popularity": 0.72,
        "rating": None,
        "district": district or pin["district"],
        "gcj_lng": lng,
        "gcj_lat": lat,
        "parent_site": "",
        "is_main_site": True,
        "address": "",
        "tel": "",
        "cost": "",
        "opentime": "",
        "cultural_score": 0.6 if poi_type in {"heritage", "museum", "culture"} else 0.35,
        "nature_score": 0.75 if poi_type == "park" else 0.2,
        "commercial_score": 0.1,
        "scenic_score": 0.7,
        "indoor_score": 0.7 if poi_type == "museum" else 0.15,
        "edu_score": 0.4 if poi_type in {"museum", "heritage"} else 0.15,
        "is_synthetic": True,
        "is_ephemeral": True,
        "geo_quality": quality,
        "supplement_reason": (reason or "")[:80],
        "_travel_alias": travel_alias or "",
        "_geo_uncertainty": geo_uncertainty_factor(quality),
    }


def sanitize_supplement(raw: dict[str, Any] | None, *, name: str, city: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("name") or name).strip()[:24]
    if len(label) < 2:
        return None
    district = str(raw.get("district") or "").strip()[:20]
    category = str(raw.get("category") or raw.get("poi_type_en") or "").strip()
    if category not in ALLOWED_SYNTHETIC_TYPES:
        category = "attraction"
    lng = _as_float(raw.get("gcj_lng", raw.get("lng", raw.get("longitude"))))
    lat = _as_float(raw.get("gcj_lat", raw.get("lat", raw.get("latitude"))))
    # Drop cross-city coordinates instead of clamping them onto the city edge.
    if lng is not None and lat is not None and not coords_in_city(city, lng, lat):
        lng, lat = None, None
        district = ""
    reason = str(raw.get("reason") or raw.get("short_reason") or "").strip()[:80]
    evidence_raw = raw.get("evidence")
    evidence = (
        [str(item)[:80] for item in evidence_raw[:5]]
        if isinstance(evidence_raw, list)
        else []
    )
    return {
        "name": label,
        "city": city,
        "district": district,
        "poi_type_en": category,
        "category_name": str(raw.get("category_name") or ""),
        "gcj_lng": lng,
        "gcj_lat": lat,
        "reason": reason,
        "source": str(raw.get("source") or "").strip()[:40],
        "evidence": evidence,
    }


def apply_distance_aliases(distances: dict, city: str) -> dict:
    """Copy travel edges from a graph node onto curated alias ids (in memory)."""
    aliases = DISTANCE_ALIASES.get(city) or {}
    if not aliases:
        return distances
    for new_id, source_id in aliases.items():
        _alias_graph_node(distances, new_id, source_id)
    return distances


def alias_graph_node(distances: dict, new_id: str, source_id: str) -> None:
    _alias_graph_node(distances, new_id, source_id)


def _alias_graph_node(distances: dict, new_id: str, source_id: str) -> None:
    new_id = str(new_id)
    source_id = str(source_id)
    if not new_id or source_id not in distances or new_id == source_id:
        return
    source_edges = distances.get(source_id) or {}
    if new_id not in distances:
        distances[new_id] = {key: dict(edge) if isinstance(edge, dict) else edge for key, edge in source_edges.items()}
    hop = {
        "straight_m": 180,
        "travel_min": 3.0,
        "travel_m": 200,
        "mode": "walk",
        "walk_min": 3.0,
        "transit_min": None,
        "transit_transfers": 0,
    }
    distances[new_id][source_id] = dict(hop)
    if source_id in distances and new_id not in distances[source_id]:
        distances[source_id] = dict(distances[source_id])
        distances[source_id][new_id] = dict(hop)
    for origin, edges in list(distances.items()):
        if origin in {new_id, source_id} or not isinstance(edges, dict):
            continue
        if source_id in edges and new_id not in edges:
            distances[origin] = dict(edges)
            distances[origin][new_id] = dict(edges[source_id])


def _clamp_city_coords(city: str, lng: float, lat: float) -> tuple[float, float]:
    west, east, south, north = CITY_BOUNDS.get(city, CITY_BOUNDS["beijing"])
    return max(west, min(east, lng)), max(south, min(north, lat))


def coords_in_city(city: str, lng: float, lat: float, *, margin: float = 0.05) -> bool:
    """True when coordinates fall inside the city's bounding box (with small margin)."""
    west, east, south, north = CITY_BOUNDS.get(city, CITY_BOUNDS["beijing"])
    return (west - margin) <= lng <= (east + margin) and (south - margin) <= lat <= (
        north + margin
    )


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
