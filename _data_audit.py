import numpy as np
import pandas as pd

serving = pd.read_csv("data/processed/poi_shanghai_recommendation.csv")
dwell = pd.read_csv("data/processed/dwell_time.csv")

print("=== serving columns ===")
print(list(serving.columns))
print("rows:", len(serving))

print()
print("=== dwell_time.csv ===")
print("rows:", len(dwell), "| cities:", dwell["city"].value_counts().to_dict())
print("sources:", dwell["source"].value_counts().to_dict())
print(dwell[["dwell_min_low", "dwell_min_high", "dwell_mu", "dwell_sigma"]].describe().round(3))
print("exp(dwell_mu) sample:", np.round(np.exp(dwell["dwell_mu"].head(5)), 1).tolist())
print("unique (low,high) pairs:", sorted(set(zip(dwell.dwell_min_low, dwell.dwell_min_high))))

sh_ids = set(serving["poi_id"].astype(str))
dwell_ids = set(dwell["poi_id"].astype(str))
print("shanghai serving covered by dwell_time:", len(sh_ids & dwell_ids), "/", len(sh_ids))

print()
print("=== popularity saturation (serving) ===")
pop = serving["popularity"].astype(float)
print(pop.describe().round(4).to_dict())
print("top value counts:", pop.value_counts().head(6).to_dict())
print("distinct values:", pop.nunique(), "| share at max:", round(float((pop == pop.max()).mean()), 3))
if "rating" in serving.columns:
    print("rating:", serving["rating"].describe().round(3).to_dict())

print()
print("=== route candidates only ===")
flag = [c for c in serving.columns if "route_candidate" in c or "candidate" in c]
print("candidate flag columns:", flag)
if flag:
    col = flag[0]
    cand = serving[serving[col].astype(str).isin(["True", "true", "1"])]
    print("candidates:", len(cand))
    print("by poi_type_en:", cand["poi_type_en"].value_counts().to_dict())
    print("by district top8:", cand["district"].value_counts().head(8).to_dict())
    p = cand["popularity"].astype(float)
    print("candidate popularity distinct:", p.nunique(), "share at max:", round(float((p == p.max()).mean()), 3))

print()
print("=== heritage label sanity ===")
her = serving[serving["poi_type_en"] == "heritage"]
print("heritage rows:", len(her))
print(her[["name", "district"]].head(20).to_string(index=False))

print()
print("=== opening hours coverage ===")
for col in serving.columns:
    if "open" in col.lower() or "time" in col.lower():
        nonnull = serving[col].notna().sum()
        print(f"  {col}: non-null {nonnull}/{len(serving)}")

print()
print("=== scene / category columns ===")
for col in serving.columns:
    if "scene" in col.lower() or "tag" in col.lower():
        print(f"  {col}: {serving[col].dropna().astype(str).value_counts().head(6).to_dict()}")
