from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    STABLE = "stable"
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class KnowledgeEvidence:
    layer: str
    source: str
    claim: str
    freshness: Freshness
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["freshness"] = self.freshness.value
        return data


@dataclass(slots=True)
class StableKnowledgeDocument:
    doc_id: str
    poi_id: str
    city: str
    name: str
    aliases: list[str]
    summary: str
    experience_tags: list[str]
    culture_tags: list[str]
    architecture_tags: list[str]
    suitable_for: list[str]
    avoid_if: list[str]
    source: str
    source_version: str

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.name,
                *self.aliases,
                self.summary,
                *self.experience_tags,
                *self.culture_tags,
                *self.architecture_tags,
                *self.suitable_for,
                *self.avoid_if,
            ]
        )


@dataclass(slots=True)
class StructuredPOI:
    poi_id: str
    city: str
    name: str
    poi_type: str
    category: str
    district: str
    address: str
    longitude: float
    latitude: float
    rating: float | None
    popularity: float
    opening_hours_raw: str
    opening_windows: list[tuple[str, str]]
    opening_hours_verified: bool
    quality_flags: list[str]
    attributes: dict[str, float]
    record: dict[str, Any] = field(repr=False)


@dataclass(slots=True)
class RealtimePOIState:
    poi_id: str
    open_status: str = "unknown"
    crowd_level: str = "unknown"
    reservation_status: str = "unknown"
    transit_status: str = "unknown"
    message: str = ""


@dataclass(slots=True)
class RealtimeNotice:
    category: str
    message: str
    source: str
    requires_confirmation: bool = True
    poi_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RealtimeSnapshot:
    city: str
    provider: str
    observed_at: datetime
    expires_at: datetime | None
    availability: str
    weather: str = "unknown"
    poi_states: dict[str, RealtimePOIState] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def freshness(self, now: datetime) -> Freshness:
        if self.availability != "available":
            return Freshness.UNKNOWN
        if self.expires_at is not None and now > self.expires_at:
            return Freshness.STALE
        return Freshness.FRESH


@dataclass(slots=True)
class CandidateContext:
    poi: StructuredPOI
    score: float
    stable_score: float
    realtime_adjustment: float
    excluded_reason: str | None
    evidence: list[KnowledgeEvidence]
    stable_document: StableKnowledgeDocument | None = None
    realtime_state: RealtimePOIState | None = None
    realtime_notices: list[RealtimeNotice] = field(default_factory=list)

    def planner_record(self) -> dict[str, Any]:
        record = dict(self.poi.record)
        record["_score"] = self.score
        record["_stable_score"] = self.stable_score
        record["_stable_summary"] = self.stable_document.summary if self.stable_document else ""
        record["_knowledge_evidence"] = [item.to_dict() for item in self.evidence]
        record["_realtime_status"] = (
            asdict(self.realtime_state) if self.realtime_state else {"availability": "unknown"}
        )
        record["_realtime_notices"] = [notice.to_dict() for notice in self.realtime_notices]
        return record
