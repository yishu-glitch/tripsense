from __future__ import annotations

import pandas as pd


SHANGHAI_DISTRICT_CODES = {
    "黄浦区": "310101",
    "徐汇区": "310104",
    "长宁区": "310105",
    "静安区": "310106",
    "普陀区": "310107",
    "虹口区": "310109",
    "杨浦区": "310110",
    "闵行区": "310112",
    "宝山区": "310113",
    "嘉定区": "310114",
    "浦东新区": "310115",
    "金山区": "310116",
    "松江区": "310117",
    "青浦区": "310118",
    "奉贤区": "310120",
    "崇明区": "310151",
}


def split_shanghai_scope(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split strict Shanghai municipality POIs from nearby-city records.

    AMap ``adcode`` is preferred when present. The legacy dataset lacks it, so
    the official district-name mapping is used as a deterministic fallback.
    """
    working = frame.copy()
    district = working.get("adname", working.get("district", "")).astype(str).str.strip()
    if "adcode" in working.columns:
        adcode = working["adcode"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
        has_adcode = adcode.str.fullmatch(r"\d{6}")
        inside = (has_adcode & adcode.str.startswith("310")) | (
            ~has_adcode & district.isin(SHANGHAI_DISTRICT_CODES)
        )
        working["admin_code"] = adcode.where(
            has_adcode, district.map(SHANGHAI_DISTRICT_CODES).fillna("")
        )
    else:
        inside = district.isin(SHANGHAI_DISTRICT_CODES)
        working["admin_code"] = district.map(SHANGHAI_DISTRICT_CODES).fillna("")

    working["admin_scope"] = "shanghai_city"
    included = working[inside].copy()
    excluded = working[~inside].copy()
    excluded["admin_scope"] = "nearby_city_phase2"
    excluded["scope_exclusion_reason"] = "outside_shanghai_municipality"
    return included.reset_index(drop=True), excluded.reset_index(drop=True)
