import unittest
from pathlib import Path

import pandas as pd

from tripsense.knowledge.hierarchy import (
    SiteDefinition,
    discover_embedded_parent_relations,
    discover_site_relations,
    write_product_hierarchy,
)


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.site = SiteDefinition(
            site_id="S1",
            name="示例古镇旅游区",
            anchor_poi_id="ROOT",
            aliases=["示例古镇"],
            relation_type="inside_scenic_area",
            auto_accept_radius_m=1000,
            review_radius_m=1800,
        )

    def test_discovers_unlabelled_child_from_inside_address(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "ROOT", "name": "示例古镇旅游区", "address": "古镇入口", "gcj_lng": 121.0, "gcj_lat": 31.0},
                {"poi_id": "C1", "name": "放生桥", "address": "示例古镇旅游区内", "gcj_lng": 121.001, "gcj_lat": 31.001},
            ]
        )
        results = discover_site_relations(frame, [self.site])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "accepted")
        self.assertIn("explicit_inside_address", results[0].evidence)

    def test_rejects_geographically_impossible_raw_parent(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "ROOT", "name": "示例古镇旅游区", "address": "古镇入口", "gcj_lng": 121.0, "gcj_lat": 31.0},
                {"poi_id": "C1", "name": "同名景点", "address": "另一座城市", "parent_site": "示例古镇", "gcj_lng": 122.0, "gcj_lat": 32.0},
            ]
        )
        results = discover_site_relations(frame, [self.site])
        self.assertEqual(results[0].status, "rejected")
        self.assertIn("raw_parent_geographically_inconsistent", results[0].quality_flags)

    def test_proximity_alone_requires_review(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "ROOT", "name": "示例古镇旅游区", "address": "古镇入口", "gcj_lng": 121.0, "gcj_lat": 31.0},
                {"poi_id": "C1", "name": "附近咖啡店", "address": "附近道路", "gcj_lng": 121.001, "gcj_lat": 31.001},
            ]
        )
        results = discover_site_relations(frame, [self.site])
        self.assertEqual(results[0].status, "review")

    def test_discovers_parent_omitted_from_raw_tag(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "ROOT", "name": "上海示例植物园", "poi_type_en": "park", "address": "示例路1号", "gcj_lng": 121.0, "gcj_lat": 31.0},
                {"poi_id": "C1", "name": "牡丹园", "poi_type_en": "park", "address": "示例路1号上海示例植物园内", "gcj_lng": 121.001, "gcj_lat": 31.001},
            ]
        )
        results = discover_embedded_parent_relations(frame)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].parent_poi_id, "ROOT")
        self.assertEqual(results[0].status, "accepted")

    def test_product_hierarchy_hides_matching_evidence(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "ROOT", "name": "上海示例植物园", "poi_type_en": "park", "parent_site": None, "address": "示例路1号", "gcj_lng": 121.0, "gcj_lat": 31.0},
                {"poi_id": "C1", "name": "牡丹园", "poi_type_en": "park", "parent_site": None, "address": "上海示例植物园内", "gcj_lng": 121.001, "gcj_lat": 31.001},
            ]
        )
        candidates = discover_embedded_parent_relations(frame)
        poi_path = Path(__file__).with_name("_product_fixture.csv")
        graph_path = Path(__file__).with_name("_product_fixture.json")
        try:
            write_product_hierarchy(
                frame, candidates, poi_path=poi_path, hierarchy_path=graph_path
            )
            result = pd.read_csv(poi_path).fillna("")
            child = result[result["poi_id"] == "C1"].iloc[0]
            self.assertEqual(child["site_parent_name"], "上海示例植物园")
            self.assertEqual(child["site_relation"], "inside")
            self.assertEqual(child["site_role"], "attraction")
            self.assertNotIn("evidence", result.columns)
            self.assertNotIn("site_link_status", result.columns)
            self.assertNotIn("parent_site", result.columns)
        finally:
            poi_path.unlink(missing_ok=True)
            graph_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
