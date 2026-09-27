import unittest

from tripsense.core.intent import inherit_session_intent, parse_local_intent
from tripsense.core.models import RoutePlan, Stop


class IntentTests(unittest.TestCase):
    def test_relaxed_history_request(self):
        intent = parse_local_intent("今天有点累，想慢慢看看历史建筑，大概四小时")
        self.assertEqual(intent.mode, "relaxed")
        self.assertEqual(intent.scene, "历史文化游")
        self.assertEqual(intent.available_minutes, 240)
        self.assertIn("heritage", intent.categories)

    def test_photo_mode(self):
        intent = parse_local_intent("想拍照找机位，三小时左右")
        self.assertEqual(intent.mode, "photo")
        self.assertEqual(intent.available_minutes, 180)

    def test_hour_range(self):
        intent = parse_local_intent("大概三到四个小时，想随便逛逛")
        self.assertEqual(intent.available_minutes, 210)
        self.assertEqual(intent.scene, "城市漫游")

    def test_old_buildings_maps_to_heritage_scene(self):
        intent = parse_local_intent("看老建筑，四小时", city="shanghai")
        self.assertEqual(intent.scene, "历史文化游")
        self.assertIn("heritage", intent.categories)
        self.assertNotIn("park", intent.categories)
        self.assertIn("老建筑", intent.focus_terms)
        self.assertEqual(intent.available_minutes, 240)

    def test_two_days_and_evening_are_parsed(self):
        two_days = parse_local_intent("两天慢慢走", city="shanghai")
        self.assertEqual(two_days.days, 2)
        self.assertGreaterEqual(two_days.available_minutes, 360)

        evening = parse_local_intent("傍晚去拍照三小时", city="shanghai")
        self.assertEqual(evening.mode, "photo")
        self.assertEqual(evening.start_hour, 17.0)
        self.assertEqual(evening.available_minutes, 180)

    def test_arabic_day_count_from_web_prompt(self):
        intent = parse_local_intent(
            "在上海和独自旅行，想用2天慢慢体验老建筑、街区生活和城市风景，每天大约四小时",
            city="shanghai",
        )
        self.assertEqual(intent.days, 2)
        self.assertEqual(intent.available_minutes, 240)

    def test_fixed_commitment_parses_place_and_window_not_late_start(self):
        dinner = parse_local_intent("晚上和外地朋友吃饭在静安", city="shanghai")
        self.assertEqual(dinner.prefer_near, "静安")
        self.assertIsNotNone(dinner.busy_from_hour)
        self.assertGreaterEqual(dinner.busy_from_hour, 17.0)
        self.assertIsNone(dinner.start_hour)

        noon = parse_local_intent("中午要在陆家嘴开个会", city="shanghai")
        self.assertEqual(noon.prefer_near, "陆家嘴")
        self.assertEqual(noon.busy_from_hour, 12.0)

        concert = parse_local_intent("我第一天晚上要看演唱会", city="shanghai")
        self.assertIsNone(concert.prefer_near)
        self.assertIsNotNone(concert.busy_from_hour)
        self.assertIsNone(concert.start_hour)

        # 「人少一点」must not parse as 1 o'clock busy window.
        vibe = parse_local_intent(
            "有没有更文艺一点的地方？最好人少一点，拍照好看。",
            city="beijing",
        )
        self.assertIsNone(vibe.busy_from_hour)

    def test_start_location_phrase(self):
        intent = parse_local_intent("从外滩出发看老建筑", city="shanghai")
        self.assertEqual(intent.start_name, "外滩")
        self.assertTrue(intent.start_as_depot)

    def test_named_sights_are_want_places_not_themes(self):
        opener = parse_local_intent(
            "我打算明天去北京玩一天，早上9点出发，晚上6点前结束。"
            "我年纪大了，走不动太多路，想去些经典景点，比如天安门、故宫，还有公园什么的。",
            city="beijing",
        )
        self.assertEqual(opener.want_places, ["天安门", "故宫"])
        self.assertIn("公园", opener.focus_terms)

        swap = parse_local_intent(
            "能不能去掉故宫，换成更轻松的地方？比如像天坛这样的公园",
            city="beijing",
        )
        self.assertEqual(swap.want_places, ["天坛"])
        self.assertNotIn("故宫", swap.want_places)
        self.assertNotIn("可以坐着休息", swap.want_places)

        only_beihai = parse_local_intent(
            "要不就只去北海公园，然后早点结束，让我回去休息？",
            city="beijing",
        )
        self.assertEqual(only_beihai.want_places, ["北海公园"])

    def test_session_inherit_keeps_full_day_budget_on_place_swap(self):
        first = parse_local_intent(
            "我打算明天去北京玩一天，早上9点出发，晚上6点前结束。"
            "想去些经典景点，比如天安门、故宫，还有公园什么的。",
            "beijing",
        )
        self.assertEqual(first.available_minutes, 480)
        plan = RoutePlan(
            city="beijing",
            mode=first.mode,
            scene=first.scene,
            stops=[
                Stop(
                    "P1",
                    "天安门",
                    "景点",
                    "attraction",
                    "东城区",
                    60,
                    None,
                    1.0,
                    "",
                    arrival_time="09:00",
                )
            ],
            available_minutes=480,
            safe_budget_minutes=403,
            planned_minutes=180,
            satisfaction_probability=0.9,
            reminder_level="comfortable",
            voice="",
            day_count=1,
        )
        swap = parse_local_intent(
            "能不能去掉故宫，换成更轻松的地方？比如像天坛这样的公园",
            "beijing",
        )
        inherited = inherit_session_intent(swap, swap.utterance, current=plan, previous=first)
        self.assertEqual(inherited.available_minutes, 480)
        self.assertEqual(inherited.want_places, ["天坛"])

        short = parse_local_intent("只剩三小时了", "beijing")
        shortened = inherit_session_intent(short, short.utterance, current=plan, previous=first)
        self.assertEqual(shortened.available_minutes, 180)

    def test_realtime_changes_require_explicit_confirmation(self):
        default_intent = parse_local_intent("上海下雨了", city="shanghai")
        confirmed_intent = parse_local_intent(
            "上海下雨了",
            city="shanghai",
            apply_realtime=True,
        )

        self.assertFalse(default_intent.apply_realtime)
        self.assertTrue(confirmed_intent.apply_realtime)


if __name__ == "__main__":
    unittest.main()
