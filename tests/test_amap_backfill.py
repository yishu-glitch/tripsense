from __future__ import annotations

import unittest

from tripsense.knowledge.amap_backfill import (
    build_child_candidates,
    build_enriched_rows,
    classify_child_candidate,
)


class AmapBackfillTests(unittest.TestCase):
    def test_service_children_are_not_attractions(self) -> None:
        cases = [
            ({"name": "动物园停车场", "typecode": "150904"}, "transport"),
            ({"name": "景区售票处", "typecode": "070306"}, "service"),
            ({"name": "游客服务中心", "typecode": "070201"}, "service"),
        ]
        for child, expected_role in cases:
            role, status = classify_child_candidate(child)
            self.assertEqual(role, expected_role)
            self.assertEqual(status, "exclude_non_attraction")

    def test_scenic_child_requires_review(self) -> None:
        role, status = classify_child_candidate(
            {"name": "园中园", "typecode": "110200"}
        )
        self.assertEqual(role, "attraction_candidate")
        self.assertEqual(status, "review_candidate")

    def test_enriched_rows_do_not_overwrite_source_fields(self) -> None:
        source = [{"poi_id": "P1", "name": "原名称", "address": "原地址"}]
        cache = {
            "P1": {
                "fetched_at": "2026-09-19T00:00:00+00:00",
                "poi": {
                    "id": "P1",
                    "name": "高德名称",
                    "address": "高德地址",
                    "business": {
                        "opentime_week": "周一至周日 09:00-17:00",
                        "tel": "021-12345678",
                    },
                    "photos": [{"url": "https://example.test/photo.jpg"}],
                    "children": [],
                    "navi": {"entr_location": "121,31"},
                },
            }
        }

        row = build_enriched_rows(source, cache)[0]

        self.assertEqual(row["name"], "原名称")
        self.assertEqual(row["address"], "原地址")
        self.assertEqual(row["amap_name"], "高德名称")
        self.assertEqual(row["amap_tel"], "021-12345678")
        self.assertEqual(row["amap_detail_status"], "returned")

    def test_child_candidates_are_evidence_only(self) -> None:
        cache = {
            "P1": {
                "fetched_at": "2026-09-19T00:00:00+00:00",
                "poi": {
                    "name": "父景区",
                    "children": [
                        {"id": "C1", "name": "停车场", "typecode": "150904"},
                        {"id": "C2", "name": "内部景点", "typecode": "110200"},
                    ],
                },
            }
        }

        candidates = build_child_candidates(cache)

        self.assertEqual(candidates[0]["candidate_status"], "exclude_non_attraction")
        self.assertEqual(candidates[1]["candidate_status"], "review_candidate")
