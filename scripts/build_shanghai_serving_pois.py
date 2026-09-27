from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


SCHEMA_VERSION = "shanghai-poi-serving-v2"
EXCLUDED_DECISIONS = {"从服务库排除", "暂不入服务库", "删除子地点"}
ROLE_ELIGIBILITY = {
    "core_attraction": "default_candidate",
    "cultural_experience": "default_candidate",
    "urban_experience": "scenario_candidate",
    "indoor_leisure": "scenario_candidate",
    "commercial_support": "on_demand_candidate",
    "ordinary_store": "on_demand_only",
    "needs_review": "hold_for_review",
}
SCENE_ELIGIBILITY = {
    "landmark_sightseeing": "default_candidate",
    "history_architecture": "default_candidate",
    "museum_exhibition": "default_candidate",
    "urban_walk": "scenario_candidate",
    "park_nature": "scenario_candidate",
    "family_entertainment": "scenario_candidate",
    "indoor_leisure": "scenario_candidate",
    "commercial_support": "on_demand_candidate",
    "uncertain": "hold_for_review",
}
CORE_NAME_PARTS = {
    "外滩", "东方明珠", "上海迪士尼", "豫园", "南京路步行街", "上海博物馆",
    "上海自然博物馆", "上海科技馆", "上海天文馆", "上海野生动物园",
    "上海动物园", "朱家角古镇", "武康路", "田子坊", "新天地", "中华艺术宫",
    "中共一大纪念馆", "广富林文化遗址",
}
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*[-~至到]\s*\d{1,2}:\d{2}")


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "[]"} else text


def _valid_hours(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if "24小时" in text or TIME_PATTERN.search(text):
        return text
    return ""


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _classify_scene(row: pd.Series, override: dict | None = None) -> dict:
    name = _text(row.get("name"))
    raw_type = _text(row.get("type"))
    poi_type = _text(row.get("poi_type_en"))
    text = f"{name} {raw_type}"
    tags: set[str] = set()
    reason = ""
    confidence = "high"

    if override and override.get("primary_scene"):
        scene = override["primary_scene"]
        tags.update(override.get("scene_tags", []))
        reason = "人工维护的场景覆盖规则"
        confidence = override.get("confidence", "high")
    elif _contains(name, ("有限公司", "实验室", "SALON", "会议室", "管理有限公司")):
        scene, confidence = "uncertain", "low"
        reason = "名称更像企业、实验室或经营设施，不能仅凭地图类别作为旅行地点"
    elif _contains(text, ("动物园", "水族馆", "海洋馆", "游乐园", "主题公园", "儿童乐园")):
        scene, reason = "family_entertainment", "亲子、动物或主题娱乐类型明确"
        tags.update({"family", "entertainment"})
    elif _contains(text, ("博物馆", "美术馆", "艺术馆", "科技馆", "天文馆", "展览馆", "陈列馆")):
        scene, reason = "museum_exhibition", "博物馆或展览场馆类型明确"
        tags.update({"museum", "indoor", "education", "rainy_day"})
    elif _contains(text, ("纪念馆", "故居", "旧址", "遗址", "教堂", "天主堂", "寺", "庙", "古塔", "古桥")):
        scene, reason = "history_architecture", "历史建筑、宗教场所或纪念空间类型明确"
        tags.update({"history", "architecture", "culture"})
        if _contains(text, ("教堂", "天主堂", "寺", "庙")):
            tags.add("religion")
    elif _contains(text, ("古镇", "老街", "步行街", "商业街", "历史街区", "弄堂", "里弄", "滨江大道")) or name.endswith(("路", "街")):
        scene, reason = "urban_walk", "街区、道路或古镇适合城市漫游"
        tags.update({"citywalk", "street_life", "photo"})
    elif _contains(text, ("森林", "湿地", "植物园", "公园", "花园", "绿地", "湖", "海滩", "岛", "郊野")):
        scene, reason = "park_nature", "公园、滨水或自然休闲类型明确"
        tags.update({"nature", "outdoor", "low_energy"})
    elif _contains(raw_type, ("购物中心", "普通商场", "商场")):
        scene, reason = "indoor_leisure", "商场或商业综合体可服务雨天、休息、餐饮和购物"
        tags.update({"indoor", "shopping", "food", "rainy_day", "low_energy"})
    elif _contains(raw_type, ("特色商业街", "综合市场", "会展中心")) or poi_type == "leisure":
        scene, reason = "commercial_support", "商业、市场或会展设施仅在对应需求下召回"
        tags.update({"shopping", "food"})
    elif poi_type in {"heritage", "culture"}:
        scene, confidence = "history_architecture", "medium"
        reason = "原始类别偏历史文化，但缺少更明确的场景信号"
        tags.update({"history", "culture"})
    elif poi_type == "park":
        scene, reason = "park_nature", "原始类型为公园"
        tags.update({"nature", "outdoor", "low_energy"})
    elif poi_type == "museum":
        scene, confidence = "museum_exhibition", "medium"
        reason = "原始类型为博物馆，但名称缺少明确场馆特征"
        tags.update({"museum", "indoor", "education"})
    else:
        scene, confidence = "landmark_sightseeing", "medium"
        reason = "通用景观地点，暂按观光地标处理"
        tags.update({"sightseeing", "photo"})

    if _contains(text, ("夜景", "夜市", "灯光", "外滩")):
        tags.add("night")
    if _contains(text, ("建筑", "故居", "旧址", "教堂", "里弄", "洋房")):
        tags.add("architecture")
    if _contains(text, ("亲子", "儿童", "动物园", "游乐园", "科技馆", "自然博物馆")):
        tags.add("family")

    core = any(part in name for part in CORE_NAME_PARTS)
    if _text(row.get("site_parent_id")):
        tier = "context"
    elif scene == "commercial_support":
        tier = "support"
    elif core:
        tier = "core"
    elif bool(row.get("route_graph_eligible")):
        tier = "major"
    else:
        tier = "local"

    return {
        "primary_scene": scene,
        "scene_tags": sorted(tags),
        "scene_confidence": confidence,
        "scene_reason": reason,
        "attraction_tier": tier,
    }


def build(root: Path, output_override: Path | None = None) -> dict:
    data = root / "data"
    enriched_path = data / "processed" / "poi_shanghai_amap_enriched.csv"
    review_path = data / "audit" / "shanghai_poi_review_decisions.csv"
    hierarchy_path = data / "processed" / "shanghai_site_hierarchy_product.json"
    distance_path = data / "distance" / "dist_shanghai.json"
    output_path = output_override or data / "processed" / "poi_shanghai_recommendation.csv"
    override_path = data / "knowledge" / "shanghai_scene_overrides.json"

    frame = pd.read_csv(enriched_path, dtype={"poi_id": str, "adcode": str})
    review = pd.read_csv(review_path, dtype={"poi_id": str}).fillna("")
    review = review.drop_duplicates("poi_id", keep="last")
    frame = frame.merge(review, on="poi_id", how="left", validate="one_to_one")

    input_rows = len(frame)
    frame = frame[~frame["review_decision"].isin(EXCLUDED_DECISIONS)].copy()

    hierarchy_nodes = json.loads(hierarchy_path.read_text(encoding="utf-8"))
    hierarchy = {str(item["poi_id"]): item for item in hierarchy_nodes}
    distances = json.loads(distance_path.read_text(encoding="utf-8"))
    graph_ids = set(map(str, distances))
    overrides = json.loads(override_path.read_text(encoding="utf-8")) if override_path.exists() else {}
    override_excluded_ids = {
        str(poi_id)
        for poi_id, value in overrides.items()
        if value.get("service_action") == "exclude"
    }
    frame = frame[~frame["poi_id"].astype(str).isin(override_excluded_ids)].copy()

    # Use verified AMap detail fields where they are structurally valid.
    returned = frame["amap_detail_status"].eq("returned")
    rename = frame["review_decision"].eq("保留并更名") & frame["amap_name"].fillna("").astype(str).str.strip().ne("")
    frame.loc[rename, "name"] = frame.loc[rename, "amap_name"]
    for target, source in [
        ("address", "amap_address"),
        ("tel", "amap_tel"),
        ("rating", "amap_rating"),
        ("cost", "amap_cost"),
        ("typecode", "amap_typecode"),
        ("photos", "amap_photo_count"),
    ]:
        usable = returned & frame[source].fillna("").astype(str).str.strip().ne("")
        frame.loc[usable, target] = frame.loc[usable, source]

    frame["opentime"] = [
        _valid_hours(today) or _valid_hours(week) or _valid_hours(old)
        for today, week, old in zip(
            frame["amap_opentime_today"], frame["amap_opentime_week"], frame["opentime"]
        )
    ]

    frame["service_status"] = "active"
    frame["service_schema_version"] = SCHEMA_VERSION
    frame["realtime_required"] = True
    frame["display_name_source"] = frame["review_decision"].map(
        lambda value: "reviewed_amap_name" if value == "保留并更名" else "canonical_name"
    )

    parent_ids, parent_names, relations, site_roles, levels = [], [], [], [], []
    service_ids = set(frame["poi_id"].astype(str))
    for row in frame.to_dict("records"):
        poi_id = str(row["poi_id"])
        node = hierarchy.get(poi_id)
        if node and str(node["parent_poi_id"]) in service_ids:
            parent_ids.append(str(node["parent_poi_id"]))
            parent_names.append(_text(node.get("parent_name")))
            relations.append("inside")
            site_roles.append(_text(node.get("role")) or "attraction")
            levels.append(int(node.get("level", 1)))
        else:
            parent_ids.append("")
            parent_names.append("")
            relations.append("")
            site_roles.append("site")
            levels.append(0)
    frame["site_parent_id"] = parent_ids
    frame["site_parent_name"] = parent_names
    frame["site_relation"] = relations
    frame["site_role"] = site_roles
    frame["site_level"] = levels

    # Entrances and facility-only children are audit evidence, not travel destinations.
    facility_child = frame["site_role"].isin({"entrance", "service"})
    frame = frame[~facility_child].copy()
    frame["route_graph_eligible"] = frame["poi_id"].isin(graph_ids)
    scene_records = [
        _classify_scene(row, overrides.get(str(row["poi_id"])))
        for _, row in frame.iterrows()
    ]
    scene_frame = pd.DataFrame(scene_records, index=frame.index)
    for column in scene_frame.columns:
        frame[column] = scene_frame[column]
    # Curated type corrections (nature/commercial mislabeled as heritage, etc.).
    for poi_id, override in overrides.items():
        if "poi_type_en" not in override:
            continue
        mask = frame["poi_id"].astype(str).eq(str(poi_id))
        if mask.any():
            frame.loc[mask, "poi_type_en"] = override["poi_type_en"]
    type_override_path = data / "knowledge" / "shanghai_type_overrides.json"
    if type_override_path.exists():
        type_overrides = json.loads(type_override_path.read_text(encoding="utf-8"))
        for poi_id, patch in type_overrides.items():
            mask = frame["poi_id"].astype(str).eq(str(poi_id))
            if not mask.any():
                continue
            for key, value in patch.items():
                if key in {"name", "reason"}:
                    continue
                if key == "scene_tags" and isinstance(value, list):
                    frame.loc[mask, key] = json.dumps(value, ensure_ascii=False)
                elif key in frame.columns:
                    frame.loc[mask, key] = value
    frame["scene_tags"] = frame["scene_tags"].map(
        lambda values: values
        if isinstance(values, str)
        else json.dumps(values, ensure_ascii=False)
    )
    frame["travel_role"] = frame["primary_scene"].map(
        {
            "landmark_sightseeing": "core_attraction",
            "history_architecture": "cultural_experience",
            "museum_exhibition": "cultural_experience",
            "urban_walk": "urban_experience",
            "park_nature": "urban_experience",
            "family_entertainment": "urban_experience",
            "indoor_leisure": "indoor_leisure",
            "commercial_support": "commercial_support",
            "uncertain": "needs_review",
        }
    )
    frame["recommendation_eligibility"] = frame["primary_scene"].map(SCENE_ELIGIBILITY).fillna("hold_for_review")
    frame["scene_review_required"] = frame["scene_confidence"].eq("low") | (
        frame["scene_confidence"].eq("medium") & frame["route_graph_eligible"]
    )
    frame.loc[frame["scene_review_required"], "recommendation_eligibility"] = "hold_for_review"
    coordinate_candidate = frame["poi_id"].astype(str).str.startswith("ZTRIP_SH_")
    frame["travel_time_source"] = "distance_graph"
    frame.loc[coordinate_candidate & ~frame["route_graph_eligible"], "travel_time_source"] = "coordinate_estimate"
    frame["is_route_candidate"] = (
        (frame["route_graph_eligible"] | coordinate_candidate)
        & frame["site_level"].eq(0)
        & ~frame["recommendation_eligibility"].isin({"hold_for_review", "on_demand_only"})
    )

    # Remove source-only payloads that are too large or volatile for runtime loading.
    drop_columns = [
        "parent_site", "amap_location", "amap_photo_urls", "amap_photo_titles",
        "amap_entr_location", "amap_exit_location", "amap_navi_poiid",
    ]
    frame = frame.drop(columns=[column for column in drop_columns if column in frame.columns])
    frame = frame.sort_values(
        ["is_route_candidate", "recommendation_eligibility", "popularity", "rating"],
        ascending=[False, True, False, False],
        na_position="last",
    ).reset_index(drop=True)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")

    review_queue = frame[frame["scene_review_required"]].copy()
    review_queue = review_queue.sort_values(
        ["route_graph_eligible", "popularity", "rating"],
        ascending=[False, False, False],
        na_position="last",
    )
    review_records = []
    for row in review_queue.head(80).to_dict("records"):
        review_records.append(
            {
                "poi_id": str(row["poi_id"]),
                "name": _text(row.get("name")),
                "district": _text(row.get("district")),
                "raw_type": _text(row.get("type")),
                "suggested_scene": _text(row.get("primary_scene")),
                "suggested_tags": _text(row.get("scene_tags")),
                "reason": _text(row.get("scene_reason")),
                "route_graph_eligible": bool(row.get("route_graph_eligible")),
                "suggested_options": "landmark_sightseeing / history_architecture / museum_exhibition / urban_walk / park_nature / family_entertainment / indoor_leisure / commercial_support / exclude",
            }
        )
    (data / "audit" / "shanghai_scene_review_queue.json").write_text(
        json.dumps(review_records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_rows": input_rows,
        "output_rows": len(frame),
        "review_excluded_rows": input_rows - len(frame) - int(facility_child.sum()),
        "facility_children_removed": int(facility_child.sum()),
        "scene_override_excluded": len(override_excluded_ids),
        "route_graph_nodes": len(graph_ids),
        "route_candidates": int(frame["is_route_candidate"].sum()),
        "roles": {str(k): int(v) for k, v in frame["travel_role"].value_counts().items()},
        "primary_scenes": {str(k): int(v) for k, v in frame["primary_scene"].value_counts().items()},
        "scene_confidence": {str(k): int(v) for k, v in frame["scene_confidence"].value_counts().items()},
        "scene_review_queue": len(review_records),
        "eligibility": {str(k): int(v) for k, v in frame["recommendation_eligibility"].value_counts().items()},
        "missing_valid_hours": int(frame["opentime"].eq("").sum()),
        "output": (
            str(output_path.relative_to(root)).replace("\\", "/")
            if output_path.is_relative_to(root)
            else str(output_path)
        ),
    }
    (data / "audit" / "shanghai_serving_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Shanghai POI serving dataset")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    print(json.dumps(build(args.root.resolve(), output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
