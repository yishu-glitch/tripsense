import unittest
from pathlib import Path

from tripsense.core.journey import JourneyStore
from tripsense.core.models import RoutePlan, Stop


def sample_plan() -> RoutePlan:
    return RoutePlan(
        city="beijing",
        mode="relaxed",
        scene="历史文化游",
        stops=[
            Stop("P1", "第一站", "古迹", "heritage", "东城区", 60, 15, 1, "适合慢慢看"),
            Stop("P2", "第二站", "公园", "park", "东城区", 45, None, 0.9, "留一点空白"),
        ],
        available_minutes=240,
        safe_budget_minutes=201.6,
        planned_minutes=120,
        satisfaction_probability=0.99,
        reminder_level="spacious",
        voice="今天不赶路。",
    )


class JourneyStoreTests(unittest.TestCase):
    def test_save_and_record_actual_journey(self):
        db_path = Path(__file__).with_name("_journey_test.db")
        db_path.unlink(missing_ok=True)
        try:
            store = JourneyStore(db_path)
            journey_id = store.save(sample_plan())
            saved = store.get(journey_id)
            self.assertEqual(saved["status"], "planned")
            self.assertEqual(len(saved["stops"]), 2)

            saved = store.update_stop(
                journey_id, 1, completed=True, actual_dwell_minutes=72, note="比预想更值得停留"
            )
            self.assertEqual(saved["status"], "in_progress")
            self.assertTrue(saved["stops"][0]["completed"])
            self.assertEqual(saved["stops"][0]["actual_dwell_minutes"], 72)

            saved = store.update_stop(journey_id, 2, completed=True)
            self.assertEqual(saved["status"], "completed")
        finally:
            db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
