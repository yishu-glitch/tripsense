import os
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from tripsense.knowledge import realtime

LIVE_WEATHER_RESPONSE = {
    "status": "1",
    "count": "1",
    "info": "OK",
    "infocode": "10000",
    "lives": [
        {
            "province": "上海",
            "city": "上海市",
            "adcode": "310000",
            "weather": "中雨",
            "temperature": "24",
            "winddirection": "东",
            "windpower": "≤3",
            "humidity": "78",
            "reporttime": "2026-09-23 10:30:00",
            "temperature_float": "24.0",
            "humidity_float": "78.0",
        }
    ],
}


class AmapWeatherProviderTests(unittest.TestCase):
    def provider_type(self):
        provider_type = getattr(realtime, "AmapWeatherProvider", None)
        self.assertIsNotNone(
            provider_type,
            "AmapWeatherProvider should implement the existing realtime contract",
        )
        return provider_type

    def test_selected_city_controls_the_amap_adcode(self):
        seen_adcodes = []

        def fetch_json(url, _timeout):
            query = parse_qs(urlparse(url).query)
            seen_adcodes.append(query["city"][0])
            response = dict(LIVE_WEATHER_RESPONSE)
            response["lives"] = [dict(LIVE_WEATHER_RESPONSE["lives"][0])]
            response["lives"][0]["adcode"] = query["city"][0]
            return response

        provider = self.provider_type()("secret-key", fetch_json=fetch_json)

        shanghai = provider.get_snapshot(
            "shanghai",
            ["P1"],
            at=datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc),
        )
        beijing = provider.get_snapshot(
            "北京",
            ["P2"],
            at=datetime(2026, 9, 23, 3, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(seen_adcodes, ["310000", "110000"])
        self.assertEqual(shanghai.weather, "rain")
        self.assertEqual(beijing.availability, "available")
        self.assertEqual(set(shanghai.poi_states), {"P1"})

    def test_heavy_rain_is_normalized_for_route_adjustment(self):
        def fetch_json(_url, _timeout):
            response = dict(LIVE_WEATHER_RESPONSE)
            response["lives"] = [{**LIVE_WEATHER_RESPONSE["lives"][0], "weather": "大暴雨"}]
            return response

        provider = self.provider_type()("secret-key", fetch_json=fetch_json)

        snapshot = provider.get_snapshot("上海市", [])

        self.assertEqual(snapshot.weather, "heavy_rain")
        self.assertEqual(snapshot.provider, "amap-weather")

    def test_api_failure_degrades_to_unavailable_without_exposing_the_key(self):
        def fetch_json(_url, _timeout):
            raise TimeoutError("request URL contained secret-key")

        provider = self.provider_type()("secret-key", fetch_json=fetch_json)

        snapshot = provider.get_snapshot("shanghai", ["P1"])

        self.assertEqual(snapshot.availability, "unavailable")
        self.assertEqual(snapshot.weather, "unknown")
        self.assertEqual(snapshot.poi_states["P1"].open_status, "unknown")
        self.assertTrue(snapshot.warnings)
        self.assertNotIn("secret-key", " ".join(snapshot.warnings))

    def test_unsupported_city_does_not_make_a_weather_request(self):
        def fetch_json(_url, _timeout):
            self.fail("unsupported cities must not call AMap")

        provider = self.provider_type()("secret-key", fetch_json=fetch_json)

        snapshot = provider.get_snapshot("guangzhou", [])

        self.assertEqual(snapshot.availability, "unavailable")
        self.assertIn("城市编码", " ".join(snapshot.warnings))

    def test_environment_factory_uses_amap_only_when_a_key_exists(self):
        factory = getattr(realtime, "realtime_provider_from_env", None)
        self.assertIsNotNone(factory)
        previous = os.environ.get("AMAP_WEB_SERVICE_KEY")
        try:
            os.environ.pop("AMAP_WEB_SERVICE_KEY", None)
            self.assertIsInstance(factory(), realtime.UnavailableRealtimeProvider)

            os.environ["AMAP_WEB_SERVICE_KEY"] = "secret-key"
            self.assertIsInstance(factory(), self.provider_type())
        finally:
            if previous is None:
                os.environ.pop("AMAP_WEB_SERVICE_KEY", None)
            else:
                os.environ["AMAP_WEB_SERVICE_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
