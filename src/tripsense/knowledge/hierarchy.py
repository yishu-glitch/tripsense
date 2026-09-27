from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(slots=True)
class SiteDefinition:
    site_id: str
    name: str
    anchor_poi_id: str
    aliases: list[str]
    relation_type: str
    auto_accept_radius_m: float
    review_radius_m: float
    sources: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class SiteRelationCandidate:
    site_id: str
    site_name: str
    parent_poi_id: str
    child_poi_id: str
    child_name: str
    relation_type: str
    status: str
    confidence: float
    distance_m: float
    evidence: list[str]
    quality_flags: list[str]
    parent_site_raw: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_site_definitions(path: str | Path) -> list[SiteDefinition]:
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    return [SiteDefinition(**record) for record in records]


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    value = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def discover_site_relations(
    frame: pd.DataFrame, definitions: Iterable[SiteDefinition]
) -> list[SiteRelationCandidate]:
    """Discover, validate and audit POI-to-site relationships.

    Auto acceptance requires both semantic evidence and geographic consistency.
    Proximity alone only creates a review candidate, preventing dense urban areas
    from being treated as one scenic site.
    """
    working = frame.copy()
    working["poi_id"] = working["poi_id"].astype(str)
    rows_by_id = {row["poi_id"]: row for row in working.to_dict("records")}
    candidates: list[SiteRelationCandidate] = []

    for site in definitions:
        anchor = rows_by_id.get(site.anchor_poi_id)
        if anchor is None:
            continue
        anchor_lng = _number(anchor.get("gcj_lng"))
        anchor_lat = _number(anchor.get("gcj_lat"))
        aliases = _aliases(site)

        for row in working.to_dict("records"):
            poi_id = str(row["poi_id"])
            if poi_id == site.anchor_poi_id:
                continue
            name = _text(row.get("name"))
            address = _text(row.get("address"))
            raw_parent = _text(row.get("parent_site"))
            lng, lat = _number(row.get("gcj_lng")), _number(row.get("gcj_lat"))
            distance = haversine_m(anchor_lng, anchor_lat, lng, lat)

            raw_match = any(_same_label(raw_parent, alias) for alias in aliases)
            name_matches = [alias for alias in aliases if alias and alias in name]
            address_matches = [alias for alias in aliases if alias and alias in address]
            strong_address = site.name in address or (
                any(len(alias) >= 4 and alias in address for alias in aliases)
                and "内" in address
            )
            in_review_radius = distance <= site.review_radius_m

            if not (raw_match or name_matches or address_matches or in_review_radius):
                continue

            evidence: list[str] = []
            flags: list[str] = []
            score = 0.0
            if raw_match:
                evidence.append("raw_parent_match")
                score += 0.45
            if name_matches:
                evidence.append("name_alias_match")
                score += 0.25
            if address_matches:
                evidence.append("address_alias_match")
                score += 0.20
            if strong_address:
                evidence.append("explicit_inside_address")
                score += 0.25
            if distance <= site.auto_accept_radius_m:
                evidence.append("within_accept_radius")
                score += 0.20
            elif in_review_radius:
                evidence.append("within_review_radius")
                score += 0.05

            outside_accept_radius = distance > site.auto_accept_radius_m
            if raw_match and outside_accept_radius:
                flags.append("raw_parent_geographically_inconsistent")
            if raw_parent and not raw_match:
                flags.append("conflicting_raw_parent")
            if _text(row.get("poi_type_en")) == "leisure":
                flags.append("commercial_child_requires_review")

            status = "review"
            if raw_match and distance > site.review_radius_m:
                status = "rejected"
            elif score >= 0.65 and not outside_accept_radius and not flags:
                status = "accepted"

            candidates.append(
                SiteRelationCandidate(
                    site_id=site.site_id,
                    site_name=site.name,
                    parent_poi_id=site.anchor_poi_id,
                    child_poi_id=poi_id,
                    child_name=name,
                    relation_type=site.relation_type,
                    status=status,
                    confidence=round(min(score, 1.0), 2),
                    distance_m=round(distance, 1),
                    evidence=evidence,
                    quality_flags=flags,
                    parent_site_raw=raw_parent,
                )
            )

    order = {"accepted": 0, "review": 1, "rejected": 2}
    candidates.sort(
        key=lambda item: (
            item.site_name,
            order[item.status],
            -item.confidence,
            item.distance_m,
        )
    )
    return candidates


def discover_embedded_parent_relations(frame: pd.DataFrame) -> list[SiteRelationCandidate]:
    """Find omitted relations by matching full POI names in names and addresses.

    Only an explicit ``...内`` address or a child name prefixed by the complete
    parent name can be auto-accepted. Other geographic neighbours remain review
    candidates. Duplicate parent names are resolved to the nearest entity.
    """
    working = frame.copy()
    working["poi_id"] = working["poi_id"].astype(str)
    records = working.to_dict("records")
    parents_by_name: dict[str, list[dict]] = {}
    for row in records:
        name = _text(row.get("name"))
        if (
            4 <= len(name) <= 36
            and _text(row.get("poi_type_en")) != "leisure"
            and not _looks_like_child_name(name)
        ):
            parents_by_name.setdefault(name, []).append(row)

    trie = _build_trie(parents_by_name)
    results: list[SiteRelationCandidate] = []
    for child in records:
        child_id = str(child["poi_id"])
        child_name = _text(child.get("name"))
        address = _text(child.get("address"))
        name_matches = _trie_matches(trie, child_name)
        address_matches = _trie_matches(trie, address)
        matched_names = _longest_distinct_matches(name_matches | address_matches)
        for parent_name in matched_names:
            possible_parents = [
                row for row in parents_by_name[parent_name] if str(row["poi_id"]) != child_id
            ]
            if not possible_parents:
                continue
            parent, distance = min(
                (
                    (row, _row_distance_m(row, child))
                    for row in possible_parents
                ),
                key=lambda item: item[1],
            )
            prefix_match = child_name.startswith(parent_name + "-") or (
                child_name.startswith(parent_name) and len(child_name) > len(parent_name) + 1
            )
            address_match = parent_name in address
            explicit_inside = address_match and "内" in address[address.find(parent_name) :]
            if not (prefix_match or address_match):
                continue

            evidence: list[str] = []
            score = 0.0
            if prefix_match:
                evidence.append("full_parent_prefix_in_name")
                score += 0.55
            if address_match:
                evidence.append("full_parent_name_in_address")
                score += 0.35
            if explicit_inside:
                evidence.append("explicit_inside_address")
                score += 0.30
            if distance <= 3000:
                evidence.append("geographically_consistent")
                score += 0.15

            flags: list[str] = []
            if distance > 8000:
                flags.append("geographically_inconsistent")
                status = "rejected"
            elif distance <= 3000 and (explicit_inside or prefix_match) and score >= 0.65:
                status = "accepted"
            else:
                status = "review"
            if _text(child.get("poi_type_en")) == "leisure":
                flags.append("commercial_child_requires_review")
                if status == "accepted":
                    status = "review"

            results.append(
                SiteRelationCandidate(
                    site_id=f"AUTO_{parent['poi_id']}",
                    site_name=parent_name,
                    parent_poi_id=str(parent["poi_id"]),
                    child_poi_id=child_id,
                    child_name=child_name,
                    relation_type=_relation_type(parent),
                    status=status,
                    confidence=round(min(score, 1.0), 2),
                    distance_m=round(distance, 1),
                    evidence=evidence,
                    quality_flags=flags,
                    parent_site_raw=_text(child.get("parent_site")),
                )
            )
    return results


def merge_relation_candidates(
    *candidate_groups: Iterable[SiteRelationCandidate],
) -> list[SiteRelationCandidate]:
    """Merge discoveries while preferring curated registry relations."""
    merged: dict[tuple[str, str], SiteRelationCandidate] = {}
    for group in candidate_groups:
        for candidate in group:
            key = (candidate.parent_poi_id, candidate.child_poi_id)
            previous = merged.get(key)
            if previous is None or (
                previous.site_id.startswith("AUTO_")
                and not candidate.site_id.startswith("AUTO_")
            ):
                merged[key] = candidate
    order = {"accepted": 0, "review": 1, "rejected": 2}
    return sorted(
        merged.values(),
        key=lambda item: (
            item.site_name,
            order[item.status],
            -item.confidence,
            item.distance_m,
        ),
    )


def write_hierarchy_outputs(
    candidates: Iterable[SiteRelationCandidate],
    *,
    candidates_path: str | Path,
    hierarchy_path: str | Path,
) -> dict:
    records = [candidate.to_dict() for candidate in candidates]
    candidates_file = Path(candidates_path)
    hierarchy_file = Path(hierarchy_path)
    candidates_file.parent.mkdir(parents=True, exist_ok=True)
    hierarchy_file.parent.mkdir(parents=True, exist_ok=True)
    with candidates_file.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    accepted = [record for record in records if record["status"] == "accepted"]
    grouped: dict[str, dict] = {}
    for record in accepted:
        site = grouped.setdefault(
            record["site_id"],
            {
                "site_id": record["site_id"],
                "site_name": record["site_name"],
                "parent_poi_id": record["parent_poi_id"],
                "children": [],
            },
        )
        site["children"].append(
            {
                "poi_id": record["child_poi_id"],
                "name": record["child_name"],
                "relation_type": record["relation_type"],
                "confidence": record["confidence"],
                "distance_m": record["distance_m"],
                "evidence": record["evidence"],
            }
        )
    hierarchy_file.write_text(
        json.dumps(list(grouped.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "total": len(records),
        "accepted": len(accepted),
        "review": sum(record["status"] == "review" for record in records),
        "rejected": sum(record["status"] == "rejected" for record in records),
        "sites_with_accepted_children": len(grouped),
    }


def write_product_hierarchy(
    frame: pd.DataFrame,
    candidates: Iterable[SiteRelationCandidate],
    *,
    poi_path: str | Path,
    hierarchy_path: str | Path,
) -> dict:
    """Write the small hierarchy contract consumed by product features.

    Matching evidence and quality flags deliberately stay in the audit file.
    The planner only needs identity, containment, node role and hierarchy depth.
    """
    accepted_by_child: dict[str, list[SiteRelationCandidate]] = {}
    for candidate in candidates:
        if candidate.status == "accepted":
            accepted_by_child.setdefault(candidate.child_poi_id, []).append(candidate)

    direct_parent: dict[str, SiteRelationCandidate] = {}
    for child_id, relations in accepted_by_child.items():
        direct_parent[child_id] = sorted(
            relations,
            key=lambda item: (-len(item.site_name), -item.confidence, item.distance_m),
        )[0]

    levels: dict[str, int] = {}

    def level_of(poi_id: str, trail: set[str] | None = None) -> int:
        if poi_id in levels:
            return levels[poi_id]
        trail = set() if trail is None else set(trail)
        if poi_id in trail:
            levels[poi_id] = 0
            return 0
        trail.add(poi_id)
        relation = direct_parent.get(poi_id)
        if relation is None:
            levels[poi_id] = 0
        else:
            levels[poi_id] = level_of(relation.parent_poi_id, trail) + 1
        return levels[poi_id]

    output = frame.copy()
    output["poi_id"] = output["poi_id"].astype(str)
    if "parent_site" in output.columns:
        output = output.drop(columns=["parent_site"])
    product_rows: list[dict] = []
    for row in output.to_dict("records"):
        poi_id = str(row["poi_id"])
        relation = direct_parent.get(poi_id)
        linked = relation is not None
        row.update(
            {
                "site_parent_id": relation.parent_poi_id if linked else "",
                "site_parent_name": relation.site_name if linked else "",
                "site_relation": "inside" if linked else "",
                "site_role": _product_role(row, linked=linked),
                "site_level": level_of(poi_id),
            }
        )
        product_rows.append(row)

    product_frame = pd.DataFrame(product_rows)
    destination = Path(poi_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    product_frame.to_csv(destination, index=False, encoding="utf-8-sig")

    nodes = []
    row_by_id = {str(row["poi_id"]): row for row in product_rows}
    for child_id, relation in direct_parent.items():
        child = row_by_id[child_id]
        nodes.append(
            {
                "poi_id": child_id,
                "name": _text(child.get("name")),
                "parent_poi_id": relation.parent_poi_id,
                "parent_name": relation.site_name,
                "relation": "inside",
                "role": child["site_role"],
                "level": child["site_level"],
            }
        )
    graph_path = Path(hierarchy_path)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "rows": len(product_frame),
        "linked": len(direct_parent),
        "standalone": len(product_frame) - len(direct_parent),
        "max_level": int(product_frame["site_level"].max()),
    }


def _aliases(site: SiteDefinition) -> list[str]:
    return sorted({site.name, *site.aliases}, key=len, reverse=True)


def _same_label(left: str, right: str) -> bool:
    normalize = lambda value: value.replace("上海", "").replace("市", "").strip()
    return bool(left and right and normalize(left) == normalize(right))


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text in {"[]", "nan", "None"} else text


def _number(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(result) else result


def _build_trie(names: dict[str, list[dict]]) -> dict:
    root: dict = {}
    for name in names:
        node = root
        for character in name:
            node = node.setdefault(character, {})
        node.setdefault("", set()).add(name)
    return root


def _trie_matches(trie: dict, text: str) -> set[str]:
    matches: set[str] = set()
    for start in range(len(text)):
        node = trie
        for character in text[start:]:
            node = node.get(character)
            if node is None:
                break
            matches.update(node.get("", set()))
    return matches


def _longest_distinct_matches(matches: set[str]) -> list[str]:
    ordered = sorted(matches, key=len, reverse=True)
    kept: list[str] = []
    for match in ordered:
        if not any(match in longer for longer in kept):
            kept.append(match)
    return kept


def _looks_like_child_name(name: str) -> bool:
    return any(separator in name for separator in ["-", "—"]) or name.endswith(
        ("入口", "出口", "售票处", "服务中心", "停车场", "码头")
    )


def _row_distance_m(parent: dict, child: dict) -> float:
    return haversine_m(
        _number(parent.get("gcj_lng")),
        _number(parent.get("gcj_lat")),
        _number(child.get("gcj_lng")),
        _number(child.get("gcj_lat")),
    )


def _relation_type(parent: dict) -> str:
    poi_type = _text(parent.get("poi_type_en"))
    if poi_type == "museum":
        return "inside_venue"
    if poi_type == "park":
        return "inside_park"
    return "inside_scenic_area"


def _product_role(row: dict, *, linked: bool) -> str:
    if not linked:
        return "site"
    name = _text(row.get("name"))
    poi_type = _text(row.get("poi_type_en"))
    if any(word in name for word in ["入口", "出口", "检票口", "大门"]):
        return "entrance"
    if any(word in name for word in ["游客中心", "服务中心", "售票处", "停车场", "卫生间"]):
        return "service"
    if any(word in name for word in ["码头", "车站", "接驳"]):
        return "transport"
    if any(word in name for word in ["打卡位", "观景台", "观景平台"]):
        return "viewpoint"
    if poi_type == "leisure":
        return "commercial"
    if poi_type == "museum" or any(word in name for word in ["展馆", "博物馆", "陈列馆"]):
        return "exhibit"
    return "attraction"
