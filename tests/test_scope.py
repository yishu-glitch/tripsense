import unittest

import pandas as pd

from tripsense.knowledge.scope import split_shanghai_scope


class ShanghaiScopeTests(unittest.TestCase):
    def test_keeps_only_official_shanghai_districts(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "SH", "district": "黄浦区"},
                {"poi_id": "KS", "district": "昆山市"},
                {"poi_id": "TC", "district": "太仓市"},
            ]
        )
        included, excluded = split_shanghai_scope(frame)
        self.assertEqual(included["poi_id"].tolist(), ["SH"])
        self.assertEqual(set(excluded["poi_id"]), {"KS", "TC"})
        self.assertEqual(included.iloc[0]["admin_code"], "310101")

    def test_adcode_overrides_incorrect_district_name(self):
        frame = pd.DataFrame(
            [
                {"poi_id": "OUT", "district": "黄浦区", "adcode": "320583"},
                {"poi_id": "IN", "district": "错误名称", "adcode": "310115"},
            ]
        )
        included, excluded = split_shanghai_scope(frame)
        self.assertEqual(included["poi_id"].tolist(), ["IN"])
        self.assertEqual(excluded["poi_id"].tolist(), ["OUT"])


if __name__ == "__main__":
    unittest.main()
