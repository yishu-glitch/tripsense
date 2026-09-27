import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class ShanghaiServingDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = pd.read_csv(
            ROOT / "data" / "processed" / "poi_shanghai_recommendation.csv",
            dtype={"poi_id": str},
        )

    def test_runtime_contract_columns_exist(self):
        required = {
            "poi_id", "name", "city", "poi_type_en", "category_name", "district",
            "address", "gcj_lng", "gcj_lat", "rating", "popularity", "opentime",
            "travel_role", "recommendation_eligibility", "service_status",
            "is_route_candidate", "realtime_required", "site_parent_id", "site_role",
            "primary_scene", "scene_tags", "scene_confidence", "scene_reason",
            "attraction_tier", "scene_review_required", "travel_time_source",
        }
        self.assertFalse(required - set(self.frame.columns))

    def test_excluded_review_rows_are_absent(self):
        decisions = pd.read_csv(
            ROOT / "data" / "audit" / "shanghai_poi_review_decisions.csv",
            dtype={"poi_id": str},
        )
        excluded = set(
            decisions.loc[
                decisions["review_decision"].isin({"从服务库排除", "暂不入服务库", "删除子地点"}),
                "poi_id",
            ]
        )
        self.assertFalse(excluded & set(self.frame["poi_id"]))

    def test_route_candidates_are_top_level_and_have_travel_time_source(self):
        graph = json.loads((ROOT / "data" / "distance" / "dist_shanghai.json").read_text(encoding="utf-8"))
        candidates = self.frame[self.frame["is_route_candidate"]]
        self.assertTrue(candidates["site_level"].eq(0).all())
        graph_rows = candidates[candidates["travel_time_source"].eq("distance_graph")]
        self.assertTrue(set(graph_rows["poi_id"]).issubset(set(graph)))
        estimated = candidates[candidates["travel_time_source"].eq("coordinate_estimate")]
        self.assertTrue(estimated["poi_id"].str.startswith("ZTRIP_SH_").all())

    def test_opening_hours_do_not_contain_known_business_area_noise(self):
        self.assertNotIn("上海虹桥商务区", set(self.frame["opentime"].fillna("")))

    def test_synthetic_landmarks_are_not_held_for_review(self):
        synthetic = self.frame[self.frame["poi_id"].str.startswith("ZTRIP_SH_")]
        self.assertFalse(synthetic.empty)
        self.assertFalse(synthetic["recommendation_eligibility"].eq("hold_for_review").any())
        self.assertTrue(synthetic["is_route_candidate"].all())

    def test_business_like_false_museum_is_held(self):
        row = self.frame[self.frame["name"].str.contains("H-Lab动物诊断参考实验室")]
        self.assertTrue(row.empty or row.iloc[0]["recommendation_eligibility"] == "hold_for_review")

    def test_false_heritage_nature_sites_are_relabeled(self):
        frame = self.frame[self.frame["is_route_candidate"]]
        by_name = frame.set_index("name")
        for name, expected in {
            "佘山国家旅游度假区": "park",
            "滴水湖": "park",
            "美兰湖景区": "park",
            "长兴岛": "park",
            "LV巨轮": "leisure",
            "东方明珠广播电视塔": "attraction",
            "上海千古情景区": "attraction",
            "滨江大道": "attraction",
        }.items():
            self.assertEqual(by_name.loc[name, "poi_type_en"], expected, name)
            self.assertNotEqual(by_name.loc[name, "poi_type_en"], "heritage", name)

    def test_architecture_streets_remain_heritage(self):
        frame = self.frame[self.frame["is_route_candidate"]]
        by_name = frame.set_index("name")
        for name in ("武康路", "思南路", "田子坊", "外滩源"):
            self.assertEqual(by_name.loc[name, "poi_type_en"], "heritage", name)


if __name__ == "__main__":
    unittest.main()
