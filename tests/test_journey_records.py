import shutil
import unittest
from pathlib import Path

from tripsense.core.journey import JourneyStore
from tripsense.core.models import RoutePlan, Stop


def sample_plan() -> RoutePlan:
    return RoutePlan(
        city="shanghai",
        mode="relaxed",
        scene="城市漫游",
        stops=[
            Stop(
                "S1",
                "武康路",
                "历史街区",
                "heritage",
                "徐汇区",
                65,
                18,
                1.0,
                "适合慢慢走",
            ),
            Stop(
                "S2",
                "思南路",
                "历史街区",
                "heritage",
                "黄浦区",
                55,
                None,
                0.9,
                "把时间留给街巷",
            ),
        ],
        available_minutes=240,
        safe_budget_minutes=200,
        planned_minutes=138,
        satisfaction_probability=0.95,
        reminder_level="spacious",
        voice="今天不用赶路。",
    )


class JourneyRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(__file__).with_name("_journey_records_test")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.db_path = self.temp_dir / "nested" / "tripsense.db"
        self.store = JourneyStore(self.db_path)
        self.journey_id = self.store.save(sample_plan())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_store_creates_parent_directory_and_lists_saved_journeys(self):
        self.assertTrue(self.db_path.exists())

        journeys = self.store.list()

        self.assertEqual(len(journeys), 1)
        self.assertEqual(journeys[0]["id"], self.journey_id)
        self.assertEqual(journeys[0]["record_count"], 0)
        self.assertEqual(journeys[0]["diary_count"], 0)

    def test_record_keeps_custom_mood_note_photos_and_actual_place(self):
        record = self.store.add_record(
            self.journey_id,
            day_index=1,
            stop_position=None,
            place_name="偶然走进的小书店",
            mood="很松弛",
            note="没有按计划走完，但这样刚刚好。",
            photo_refs=["local://photo-1.jpg", "local://photo-2.jpg"],
        )

        self.assertEqual(record["mood"], "很松弛")
        self.assertEqual(record["place_name"], "偶然走进的小书店")
        self.assertEqual(record["photo_refs"], ["local://photo-1.jpg", "local://photo-2.jpg"])
        self.assertEqual(self.store.list()[0]["record_count"], 1)

    def test_record_can_inherit_place_name_from_a_planned_stop(self):
        record = self.store.add_record(
            self.journey_id,
            day_index=1,
            stop_position=1,
            mood="舒服",
            note="树影很好看。",
        )

        self.assertEqual(record["place_name"], "武康路")
        self.assertEqual(record["stop_position"], 1)

    def test_diary_requires_at_least_one_record(self):
        with self.assertRaisesRegex(ValueError, "at least one journey record"):
            self.store.generate_diary(self.journey_id)

    def test_diary_is_generated_from_route_and_records(self):
        self.store.add_record(
            self.journey_id,
            day_index=1,
            stop_position=1,
            mood="惊喜",
            note="转角看见梧桐树下的老房子。",
            photo_refs=["local://wukang.jpg"],
        )

        diary = self.store.generate_diary(self.journey_id)

        self.assertEqual(diary["journey_id"], self.journey_id)
        self.assertEqual(diary["source_record_count"], 1)
        self.assertEqual(diary["days"][0]["day_index"], 1)
        self.assertEqual(diary["days"][0]["entries"][0]["place_name"], "武康路")
        self.assertEqual(
            diary["days"][0]["entries"][0]["note"],
            "转角看见梧桐树下的老房子。",
        )
        self.assertEqual(self.store.latest_diary(self.journey_id)["id"], diary["id"])
        self.assertEqual(self.store.list()[0]["diary_count"], 1)


if __name__ == "__main__":
    unittest.main()
