from __future__ import annotations

import re
from datetime import time
from typing import Iterable

import pandas as pd

from .contracts import StructuredPOI


ATTR_COLUMNS = [
    "cultural_score", "nature_score", "commercial_score",
    "scenic_score", "indoor_score", "edu_score",
]

TIME_RANGE_PATTERN = re.compile(
    r"(?P<start>\d{1,2}:\d{2})\s*[-~至到]\s*(?P<end>\d{1,2}:\d{2})"
)


def parse_opening_hours(raw: object) -> tuple[list[tuple[str, str]], bool, list[str]]:
    value = "" if raw is None else str(raw).strip()
    if not value or value.lower() in {"nan", "[]"}:
        return [], False, ["missing_opening_hours"]
    if value in {"全天", "24小时", "24 小时", "24小时营业", "24 小时营业"}:
        return [("00:00", "23:59")], True, []
    matches = [(item.group("start"), item.group("end")) for item in TIME_RANGE_PATTERN.finditer(value)]
    if matches:
        return matches, True, []
    return [], False, ["unverified_opening_hours"]


class StructuredPOIRepository:
    def __init__(self, frame: pd.DataFrame, distances: dict):
        self.frame = frame.copy()
        self.frame["poi_id"] = self.frame["poi_id"].astype(str)
        self.distances = distances

    def get(self, poi_id: str) -> StructuredPOI | None:
        rows = self.frame[self.frame["poi_id"] == str(poi_id)]
        if rows.empty:
            return None
        return self._convert(rows.iloc[0].to_dict())

    def filter(
        self,
        *,
        city: str,
        allowed_types: Iterable[str] | None = None,
        excluded_types: Iterable[str] | None = None,
        districts: Iterable[str] | None = None,
        graph_only: bool = True,
        include_ids: Iterable[str] | None = None,
    ) -> list[StructuredPOI]:
        frame = self.frame
        if "city" in frame.columns:
            city_names = {"shanghai": "上海", "beijing": "北京"}
            expected = city_names.get(city, city)
            frame = frame[frame["city"].astype(str).str.contains(expected, na=False)]
        if "service_status" in frame.columns:
            frame = frame[frame["service_status"].eq("active")]
        if graph_only:
            eligible_ids = set(map(str, self.distances.keys()))
            if include_ids:
                eligible_ids.update(map(str, include_ids))
            if "is_route_candidate" in frame.columns:
                route_mask = frame["is_route_candidate"].astype(str).str.lower().eq("true")
                if include_ids:
                    route_mask |= frame["poi_id"].isin(set(map(str, include_ids)))
                frame = frame[route_mask]
            else:
                frame = frame[frame["poi_id"].isin(eligible_ids)]
        if allowed_types:
            allowed = frame["poi_type_en"].isin(set(allowed_types))
            if include_ids:
                allowed = allowed | frame["poi_id"].isin(set(map(str, include_ids)))
            frame = frame[allowed]
        if excluded_types:
            frame = frame[~frame["poi_type_en"].isin(set(excluded_types))]
        if districts:
            frame = frame[frame["district"].isin(set(districts))]
        return [self._convert(row) for row in frame.to_dict("records")]

    def _convert(self, row: dict) -> StructuredPOI:
        windows, verified, flags = parse_opening_hours(row.get("opentime"))
        if pd.isna(row.get("rating")):
            flags.append("missing_rating")
        if not str(row.get("address", "")).strip() or str(row.get("address", "")).lower() == "nan":
            flags.append("missing_address")
        return StructuredPOI(
            poi_id=str(row["poi_id"]),
            city=str(row.get("city", "")),
            name=str(row.get("name", "")),
            poi_type=str(row.get("poi_type_en", "")),
            category=str(row.get("category_name", "")),
            district=str(row.get("district", "")),
            address=str(row.get("address", "")),
            longitude=float(row.get("gcj_lng", 0) or 0),
            latitude=float(row.get("gcj_lat", 0) or 0),
            rating=None if pd.isna(row.get("rating")) else float(row["rating"]),
            popularity=float(row.get("popularity", 0.5) or 0.5),
            opening_hours_raw=str(row.get("opentime", "")),
            opening_windows=windows,
            opening_hours_verified=verified,
            quality_flags=flags,
            attributes={column: float(row.get(column, 0) or 0) for column in ATTR_COLUMNS},
            record=row,
        )
