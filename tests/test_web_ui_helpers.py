import json
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required for UI helper tests")
class WebUiHelperTests(unittest.TestCase):
    def run_helper(self, script_body: str):
        helper_path = Path(__file__).parents[1] / "web" / "tripsense-ui.js"
        script = f"""
const ui = require({json.dumps(str(helper_path))});
const result = (() => {{ {script_body} }})();
process.stdout.write(JSON.stringify(result));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_record_count_is_based_on_journeys_with_records(self):
        result = self.run_helper(
            "return ui.countRecordedJourneys({ slow: 5, light: 0, beijing: 2 });"
        )

        self.assertEqual(result, 2)

    def test_trip_filter_matches_city_and_supports_all(self):
        result = self.run_helper(
            """
const trips = [
  { id: 'slow', city: '上海' },
  { id: 'light', city: '上海' },
  { id: 'hutong', city: '北京' }
];
return {
  shanghai: ui.filterTripsByCity(trips, '上海').map(item => item.id),
  beijing: ui.filterTripsByCity(trips, '北京').map(item => item.id),
  all: ui.filterTripsByCity(trips, '全部').map(item => item.id)
};
"""
        )

        self.assertEqual(result["shanghai"], ["slow", "light"])
        self.assertEqual(result["beijing"], ["hutong"])
        self.assertEqual(result["all"], ["slow", "light", "hutong"])

    def test_first_visit_uses_detected_city_and_starts_signed_out(self):
        result = self.run_helper(
            """
return ui.resolveLaunchState({
  hasVisited: false,
  detectedCity: '上海',
  fallbackCity: '杭州'
});
"""
        )

        self.assertEqual(
            result,
            {"firstVisit": True, "city": "上海", "signedIn": False},
        )

    def test_returning_visit_restores_last_city_and_auth_state(self):
        result = self.run_helper(
            """
return ui.resolveLaunchState({
  hasVisited: true,
  lastCity: '北京',
  detectedCity: '上海',
  signedIn: true,
  fallbackCity: '杭州'
});
"""
        )

        self.assertEqual(
            result,
            {"firstVisit": False, "city": "北京", "signedIn": True},
        )

    def test_forced_first_visit_keeps_saved_city_and_auth_for_demo(self):
        result = self.run_helper(
            """
return ui.resolveLaunchState({
  hasVisited: true,
  forceFirst: true,
  lastCity: '杭州',
  detectedCity: '上海',
  signedIn: true,
  fallbackCity: '北京'
});
"""
        )

        self.assertEqual(
            result,
            {"firstVisit": True, "city": "杭州", "signedIn": True},
        )

    def test_first_visit_falls_back_when_location_is_unavailable(self):
        result = self.run_helper(
            """
return ui.resolveLaunchState({
  hasVisited: false,
  detectedCity: '',
  fallbackCity: '上海'
});
"""
        )

        self.assertEqual(result["city"], "上海")

    def test_trip_context_summary_exposes_missing_optional_details(self):
        result = self.run_helper(
            """
return ui.summarizeTripContext({
  city: '上海',
  companion: '',
  days: 3,
  date: ''
});
"""
        )

        self.assertEqual(result, "上海 · 同行未设置 · 3 天 · 日期待定")

    def test_planning_requires_companion_but_not_a_date(self):
        result = self.run_helper(
            """
return {
  missingCompanion: ui.tripContextReady({ companion: '', days: 3, date: '' }),
  readyWithoutDate: ui.tripContextReady({ companion: '朋友', days: 3, date: '' })
};
"""
        )

        self.assertEqual(result, {"missingCompanion": False, "readyWithoutDate": True})

    def test_rain_demo_starts_as_a_notice_without_changing_the_route(self):
        result = self.run_helper(
            "return ui.resolveWeatherDemo('?first=1&demo_weather=rain');"
        )

        self.assertEqual(
            result,
            {
                "kind": "rain",
                "stage": "notice",
                "routeAdjusted": False,
            },
        )

    def test_weather_preview_does_not_change_route_until_confirmation(self):
        result = self.run_helper(
            """
const notice = ui.resolveWeatherDemo('?demo_weather=rain');
const preview = ui.transitionWeatherDemo(notice, 'preview');
const applied = ui.transitionWeatherDemo(preview, 'apply');
const undone = ui.transitionWeatherDemo(applied, 'undo');
return { preview, applied, undone };
"""
        )

        self.assertEqual(result["preview"]["stage"], "preview")
        self.assertFalse(result["preview"]["routeAdjusted"])
        self.assertEqual(result["applied"]["stage"], "applied")
        self.assertTrue(result["applied"]["routeAdjusted"])
        self.assertEqual(result["undone"]["stage"], "notice")
        self.assertFalse(result["undone"]["routeAdjusted"])

    def test_keeping_original_route_preserves_a_reopenable_weather_notice(self):
        result = self.run_helper(
            """
const kept = ui.transitionWeatherDemo(
  ui.resolveWeatherDemo('?demo_weather=rain'),
  'keep'
);
return {
  kept,
  reopened: ui.transitionWeatherDemo(kept, 'reopen')
};
"""
        )

        self.assertEqual(result["kept"]["stage"], "kept")
        self.assertFalse(result["kept"]["routeAdjusted"])
        self.assertEqual(result["reopened"]["stage"], "notice")

    def test_rain_notice_is_compact_and_only_offers_a_preview(self):
        result = self.run_helper(
            "return ui.weatherDemoPresentation({ kind: 'rain', stage: 'notice', routeAdjusted: false });"
        )

        self.assertTrue(result["compact"])
        self.assertEqual(result["copy"], "江边与较长步行可能受影响。")
        self.assertEqual(
            result["actions"],
            [{"action": "preview", "label": "查看方案", "ariaLabel": "查看雨天方案"}],
        )

    def test_applied_rain_notice_uses_an_accessible_icon_only_undo(self):
        result = self.run_helper(
            "return ui.weatherDemoPresentation({ kind: 'rain', stage: 'applied', routeAdjusted: true });"
        )

        self.assertTrue(result["compact"])
        self.assertEqual(
            result["actions"],
            [{"action": "undo", "label": "↶", "ariaLabel": "撤销雨天调整"}],
        )

    def test_backend_weather_notice_becomes_a_live_confirmation_state(self):
        result = self.run_helper(
            """
return ui.weatherNoticeFromPlan({
  realtime_notices: [{
    category: 'weather',
    message: '当前有雨，是否需要查看室内备选？确认后我再调整路线。',
    source: 'amap-weather',
    requires_confirmation: true
  }]
}, '上海');
"""
        )

        self.assertEqual(result["kind"], "rain")
        self.assertEqual(result["stage"], "notice")
        self.assertTrue(result["live"])
        self.assertEqual(result["title"], "上海当前有雨")
        self.assertFalse(result["routeAdjusted"])

    def test_route_card_groups_days_and_marks_updates(self):
        result = self.run_helper(
            """
const draft = ui.routeCardPresentation({
  plan: {
    day_count: 2,
    stops: [
      { poi_id: 'a', name: '外滩', day_index: 1, arrival_time: '10:00' },
      { poi_id: 'b', name: '武康路', day_index: 2, arrival_time: '11:00' }
    ]
  }
});
const clarify = ui.routeCardPresentation({
  plan: {
    day_count: 2,
    stops: [
      { poi_id: 'a', name: '外滩', day_index: 1, arrival_time: '10:00' },
      { poi_id: 'b', name: '武康路', day_index: 2, arrival_time: '11:00' }
    ]
  },
  previousPlan: {
    day_count: 2,
    stops: [
      { poi_id: 'a', name: '外滩', day_index: 1, arrival_time: '10:00' },
      { poi_id: 'b', name: '武康路', day_index: 2, arrival_time: '11:00' }
    ]
  },
  needsClarification: true
});
const updated = ui.routeCardPresentation({
  previousPlan: {
    day_count: 2,
    stops: [
      { poi_id: 'a', name: '外滩', day_index: 1, arrival_time: '10:00' },
      { poi_id: 'b', name: '武康路', day_index: 2, arrival_time: '11:00' }
    ]
  },
  plan: {
    day_count: 2,
    stops: [
      { poi_id: 'a', name: '外滩', day_index: 1, arrival_time: '10:00' },
      { poi_id: 'c', name: '静安寺', day_index: 1, arrival_time: '16:20' },
      { poi_id: 'b', name: '武康路', day_index: 2, arrival_time: '11:00' }
    ]
  },
  preferNear: '静安'
});
return { draft, clarify, updated };
"""
        )

        self.assertEqual(result["draft"]["mode"], "draft")
        self.assertEqual(len(result["draft"]["days"]), 2)
        self.assertIn("外滩", result["draft"]["copy"])
        self.assertIn("武康路", result["draft"]["days"][1]["summary"])
        self.assertEqual(result["clarify"]["mode"], "clarify")
        self.assertEqual(result["clarify"]["kicker"], "路线先不动")
        self.assertEqual(result["updated"]["mode"], "updated")
        self.assertEqual(result["updated"]["kicker"], "已按你说的更新")
        self.assertIn("静安寺", result["updated"]["highlightNames"])
        self.assertIn("静安", result["updated"]["changeNote"])


if __name__ == "__main__":
    unittest.main()
