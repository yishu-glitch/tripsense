from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from .contracts import RealtimePOIState, RealtimeSnapshot

AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
DEFAULT_CITY_ADCODES = {
    "beijing": "110000",
    "北京": "110000",
    "北京市": "110000",
    "shanghai": "310000",
    "上海": "310000",
    "上海市": "310000",
}


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_amap_weather(value: str) -> str:
    weather = value.strip()
    if not weather:
        return "unknown"
    if "暴雨" in weather or "大雨" in weather:
        return "heavy_rain"
    if "雷" in weather:
        return "storm"
    if "雨" in weather:
        return "rain"
    if "雪" in weather:
        return "snow"
    if "雾" in weather or "霾" in weather:
        return "fog"
    if weather == "晴":
        return "clear"
    if "云" in weather:
        return "cloudy"
    if weather == "阴":
        return "overcast"
    return "unknown"


class RealtimeProvider(Protocol):
    def get_snapshot(
        self, city: str, poi_ids: Sequence[str], *, at: datetime | None = None
    ) -> RealtimeSnapshot: ...


class UnavailableRealtimeProvider:
    """Honest first-stage fallback: unknown is preferable to fabricated freshness."""

    provider_name = "unavailable"

    def get_snapshot(
        self, city: str, poi_ids: Sequence[str], *, at: datetime | None = None
    ) -> RealtimeSnapshot:
        observed = at or datetime.now(timezone.utc)
        return RealtimeSnapshot(
            city=city,
            provider="unavailable",
            observed_at=observed,
            expires_at=None,
            availability="unavailable",
            poi_states={str(poi_id): RealtimePOIState(str(poi_id)) for poi_id in poi_ids},
            warnings=["实时数据源尚未连接，天气、拥挤、预约和交通状态均不作确定判断。"],
        )


class StaticRealtimeProvider:
    """Injectable provider for development, demos and contract tests."""

    def __init__(
        self,
        *,
        weather: str = "unknown",
        states: dict[str, RealtimePOIState] | None = None,
        ttl_minutes: int = 15,
        provider_name: str = "static-development",
    ):
        self.weather = weather
        self.states = states or {}
        self.ttl_minutes = ttl_minutes
        self.provider_name = provider_name

    def get_snapshot(
        self, city: str, poi_ids: Sequence[str], *, at: datetime | None = None
    ) -> RealtimeSnapshot:
        observed = at or datetime.now(timezone.utc)
        return RealtimeSnapshot(
            city=city,
            provider=self.provider_name,
            observed_at=observed,
            expires_at=observed + timedelta(minutes=self.ttl_minutes),
            availability="available",
            weather=self.weather,
            poi_states={
                str(poi_id): self.states.get(str(poi_id), RealtimePOIState(str(poi_id)))
                for poi_id in poi_ids
            },
        )


class AmapWeatherProvider:
    """AMap live-weather adapter for the shared real-time contract."""

    provider_name = "amap-weather"

    def __init__(
        self,
        api_key: str,
        *,
        fetch_json: Callable[[str, float], dict[str, Any]] = _fetch_json,
        city_adcodes: dict[str, str] | None = None,
        timeout_seconds: float = 3.0,
        ttl_minutes: int = 30,
    ):
        self.api_key = api_key.strip()
        self.fetch_json = fetch_json
        self.city_adcodes = city_adcodes or DEFAULT_CITY_ADCODES
        self.timeout_seconds = timeout_seconds
        self.ttl_minutes = ttl_minutes

    def get_snapshot(
        self, city: str, poi_ids: Sequence[str], *, at: datetime | None = None
    ) -> RealtimeSnapshot:
        observed = at or datetime.now(timezone.utc)
        states = {str(poi_id): RealtimePOIState(str(poi_id)) for poi_id in poi_ids}
        adcode = self._resolve_adcode(city)
        if adcode is None:
            return self._unavailable(
                city,
                states,
                observed,
                "当前城市还没有可用的高德城市编码，天气暂时无法确认。",
            )

        query = urlencode(
            {
                "key": self.api_key,
                "city": adcode,
                "extensions": "base",
                "output": "JSON",
            }
        )
        try:
            payload = self.fetch_json(f"{AMAP_WEATHER_URL}?{query}", self.timeout_seconds)
        except (OSError, TypeError, ValueError):
            return self._unavailable(
                city,
                states,
                observed,
                "高德天气服务暂时不可用，路线仍可生成，但不会依据天气调整。",
            )

        if str(payload.get("status")) != "1" or not payload.get("lives"):
            info = str(payload.get("info") or "未知错误")
            return self._unavailable(
                city,
                states,
                observed,
                f"高德天气未返回可用数据（{info}），本次不依据天气调整。",
            )

        weather = _normalize_amap_weather(str(payload["lives"][0].get("weather", "")))
        return RealtimeSnapshot(
            city=city,
            provider=self.provider_name,
            observed_at=observed,
            expires_at=observed + timedelta(minutes=self.ttl_minutes),
            availability="available",
            weather=weather,
            poi_states=states,
        )

    def _resolve_adcode(self, city: str) -> str | None:
        normalized = city.strip()
        if len(normalized) == 6 and normalized.isdigit():
            return normalized
        return self.city_adcodes.get(normalized.lower()) or self.city_adcodes.get(normalized)

    def _unavailable(
        self,
        city: str,
        states: dict[str, RealtimePOIState],
        observed: datetime,
        warning: str,
    ) -> RealtimeSnapshot:
        return RealtimeSnapshot(
            city=city,
            provider=self.provider_name,
            observed_at=observed,
            expires_at=None,
            availability="unavailable",
            weather="unknown",
            poi_states=states,
            warnings=[warning],
        )


def realtime_provider_from_env() -> RealtimeProvider:
    api_key = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
    if not api_key:
        return UnavailableRealtimeProvider()
    return AmapWeatherProvider(api_key)
