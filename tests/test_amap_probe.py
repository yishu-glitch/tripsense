from __future__ import annotations

from io import StringIO
import unittest
from pathlib import Path
from unittest.mock import patch

from tripsense.knowledge.amap_probe import ProbeTarget, flatten_detail, select_probe_targets


class AmapProbeTests(unittest.TestCase):
    def test_select_probe_targets_skips_synthetic_ids(self) -> None:
        content = (
            "poi_id,name,district,address\n"
            "ZTRIP_SH_001,外滩,黄浦区,\n"
            "B001,外滩观光隧道,黄浦区,A\n"
        )
        with patch.object(Path, "open", return_value=StringIO(content)):
            targets = select_probe_targets(
                Path("unused.csv"), names=["外滩", "外滩观光隧道"]
            )

        self.assertEqual(targets, [ProbeTarget("B001", "外滩观光隧道", "黄浦区", "A")])

    def test_flatten_detail_extracts_advanced_fields(self) -> None:
        target = ProbeTarget("B001", "测试景点", "黄浦区", "旧地址")
        detail = {
            "id": "B001",
            "name": "测试景点",
            "address": "新地址",
            "parent": "P001",
            "business": {"opentime_today": "09:00-17:00", "rating": "4.8"},
            "photos": [{"title": "正门", "url": "https://example.test/a.jpg"}],
            "children": [{"id": "C001", "name": "子景点"}],
            "navi": {"entr_location": "121.1,31.1", "exit_location": "121.2,31.2"},
        }

        record = flatten_detail(target, detail)

        self.assertTrue(record["name_consistent"])
        self.assertEqual(record["opentime_today"], "09:00-17:00")
        self.assertEqual(record["photo_count"], 1)
        self.assertEqual(record["child_ids"], ["C001"])
        self.assertEqual(record["entr_location"], "121.1,31.1")

    def test_flatten_detail_marks_missing_result(self) -> None:
        record = flatten_detail(ProbeTarget("B404", "不存在"), None)

        self.assertFalse(record["returned"])
        self.assertFalse(record["name_consistent"])


if __name__ == "__main__":
    unittest.main()
