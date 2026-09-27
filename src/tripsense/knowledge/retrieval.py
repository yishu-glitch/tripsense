from __future__ import annotations

from datetime import datetime, timezone

from tripsense.core.models import Intent

from .contracts import CandidateContext, Freshness, KnowledgeEvidence, RealtimeNotice
from .realtime import RealtimeProvider
from .stable import LexicalStableKnowledgeStore
from .structured import StructuredPOIRepository

MODE_ATTRIBUTE_WEIGHTS = {
    "relaxed": {
        "nature_score": 0.35,
        "scenic_score": 0.35,
        "commercial_score": 0.05,
        "indoor_score": 0.05,
    },
    "full": {"cultural_score": 0.25, "scenic_score": 0.30, "edu_score": 0.15},
    "deep": {"cultural_score": 0.50, "edu_score": 0.30, "scenic_score": 0.10},
    "photo": {"scenic_score": 0.55, "nature_score": 0.25, "cultural_score": 0.10},
    "balanced": {
        "cultural_score": 0.20,
        "nature_score": 0.20,
        "scenic_score": 0.30,
        "edu_score": 0.10,
    },
}

SCENE_SEARCH_HINTS = {
    "历史文化游": "老建筑 历史 街区 建筑 故居 里弄",
    "城市漫游": "街区 漫步 街巷 城市 里弄",
    "自然公园游": "公园 自然 绿地 湖",
    "亲子研学游": "博物馆 亲子 科普 儿童",
    "休闲购物游": "商场 购物 美食",
    "综合观光游": "地标 城市 景观",
}


class LayeredKnowledgeRetriever:
    """Structured filtering -> stable retrieval -> real-time adjustment."""

    def __init__(
        self,
        structured: StructuredPOIRepository,
        stable: LexicalStableKnowledgeStore,
        realtime: RealtimeProvider,
    ):
        self.structured = structured
        self.stable = stable
        self.realtime = realtime

    def retrieve(self, intent: Intent, *, limit: int = 180) -> list[CandidateContext]:
        hint = SCENE_SEARCH_HINTS.get(intent.scene, "")
        named = " ".join(getattr(intent, "want_places", None) or [])
        query = f"{intent.utterance} {hint} {named}".strip()
        stable_hits = self.stable.search(query, city=intent.city, limit=60)
        stable_by_poi = {hit.document.poi_id: hit for hit in stable_hits}
        structured_candidates = self.structured.filter(
            city=intent.city,
            allowed_types=intent.categories,
            excluded_types=intent.excluded_categories,
            graph_only=True,
            include_ids=stable_by_poi.keys(),
        )
        if len(structured_candidates) < 2:
            structured_candidates = self.structured.filter(
                city=intent.city,
                excluded_types=intent.excluded_categories,
                graph_only=True,
                include_ids=stable_by_poi.keys(),
            )

        poi_ids = [candidate.poi_id for candidate in structured_candidates]
        snapshot = self.realtime.get_snapshot(intent.city, poi_ids)
        now = datetime.now(timezone.utc)
        freshness = snapshot.freshness(now)
        weather_notices = self._weather_notices(snapshot.weather, snapshot.provider)

        results: list[CandidateContext] = []
        for poi in structured_candidates:
            hit = stable_by_poi.get(poi.poi_id)
            stable_score = hit.score if hit else 0.0
            preference_weights = MODE_ATTRIBUTE_WEIGHTS.get(
                intent.mode, MODE_ATTRIBUTE_WEIGHTS["balanced"]
            )
            weight_total = sum(preference_weights.values()) or 1.0
            preference_score = (
                sum(
                    poi.attributes.get(name, 0.0) * weight
                    for name, weight in preference_weights.items()
                )
                / weight_total
            )
            base_score = 0.42 * poi.popularity
            if poi.rating is not None:
                base_score += 0.08 * min(poi.rating / 5.0, 1.0)
            base_score += 0.25 * preference_score
            base_score += 0.25 * stable_score

            evidence = [
                KnowledgeEvidence(
                    layer="structured",
                    source="poi_shanghai_recommendation.csv"
                    if intent.city == "shanghai"
                    else "poi_beijing_clean.csv",
                    claim=f"{poi.name}：{poi.category}，位于{poi.district}",
                    freshness=Freshness.STABLE,
                )
            ]
            if not poi.opening_hours_verified:
                evidence.append(
                    KnowledgeEvidence(
                        layer="structured",
                        source="source_data_quality_check",
                        claim="营业时间尚未验证，不能据此承诺当前开放。",
                        freshness=Freshness.UNKNOWN,
                    )
                )
            if hit:
                evidence.append(
                    KnowledgeEvidence(
                        layer="stable_rag",
                        source=hit.document.source,
                        claim=hit.document.summary,
                        freshness=Freshness.STABLE,
                    )
                )

            state = snapshot.poi_states.get(poi.poi_id)
            realtime_adjustment = 0.0
            excluded_reason = None
            realtime_notices = list(weather_notices)
            if state:
                realtime_notices.extend(self._state_notices(state, snapshot.provider, poi.name))
                if intent.apply_realtime:
                    if state.open_status == "closed":
                        excluded_reason = "实时状态显示当前关闭"
                    if state.reservation_status == "unavailable":
                        excluded_reason = "实时状态显示预约不可用"
                    if state.crowd_level == "high":
                        realtime_adjustment -= 0.12 if intent.mode == "relaxed" else 0.05
                    if state.transit_status == "disrupted":
                        realtime_adjustment -= 0.15
                if any(
                    value != "unknown"
                    for value in [
                        state.open_status,
                        state.crowd_level,
                        state.reservation_status,
                        state.transit_status,
                    ]
                ):
                    evidence.append(
                        KnowledgeEvidence(
                            layer="realtime",
                            source=snapshot.provider,
                            claim=state.message or "已获取实时状态。",
                            freshness=freshness,
                            observed_at=snapshot.observed_at.isoformat(),
                        )
                    )

            if intent.apply_realtime and snapshot.weather in {"rain", "storm", "heavy_rain"}:
                indoor = poi.attributes.get("indoor_score", 0.0)
                realtime_adjustment += 0.10 if indoor >= 0.6 else -0.10

            results.append(
                CandidateContext(
                    poi=poi,
                    score=round(base_score + realtime_adjustment, 4),
                    stable_score=stable_score,
                    realtime_adjustment=realtime_adjustment,
                    excluded_reason=excluded_reason,
                    evidence=evidence,
                    stable_document=hit.document if hit else None,
                    realtime_state=state,
                    realtime_notices=realtime_notices,
                )
            )

        results = [item for item in results if item.excluded_reason is None]
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    @staticmethod
    def _weather_notices(weather: str, source: str) -> list[RealtimeNotice]:
        messages = {
            "rain": "当前有雨，是否需要查看室内备选？确认后我再调整路线。",
            "heavy_rain": "当前有较强降雨，是否需要减少户外安排？确认后我再调整路线。",
            "storm": "当前可能有雷雨，是否需要优先考虑室内地点？确认后我再调整路线。",
            "snow": "当前有降雪，是否需要调整步行和户外安排？确认后我再调整路线。",
            "fog": "当前能见度可能受影响，是否需要调整观景安排？确认后我再调整路线。",
        }
        message = messages.get(weather)
        if not message:
            return []
        return [RealtimeNotice(category="weather", message=message, source=source)]

    @staticmethod
    def _state_notices(state, source: str, poi_name: str) -> list[RealtimeNotice]:
        notices: list[RealtimeNotice] = []
        conditions = [
            (state.open_status == "closed", "opening", f"{poi_name} 当前可能关闭"),
            (
                state.reservation_status == "unavailable",
                "reservation",
                f"{poi_name} 当前可能无法预约",
            ),
            (state.crowd_level == "high", "crowd", f"{poi_name} 当前可能较拥挤"),
            (
                state.transit_status == "disrupted",
                "traffic",
                f"前往 {poi_name} 的交通可能受到影响",
            ),
        ]
        for active, category, fallback in conditions:
            if active:
                notices.append(
                    RealtimeNotice(
                        category=category,
                        message=f"{state.message or fallback}。确认后我再调整路线。",
                        source=source,
                        poi_id=state.poi_id,
                    )
                )
        return notices
