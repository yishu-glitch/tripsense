from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Intent:
    city: str
    utterance: str
    available_minutes: int = 240
    uncertainty_minutes: int = 30
    risk_epsilon: float = 0.1
    risk_model: str = "gaussian"
    wasserstein_radius: float = 0.0
    mode: str = "balanced"
    scene: str = "综合观光游"
    categories: list[str] = field(default_factory=lambda: ["attraction", "heritage", "park"])
    excluded_categories: list[str] = field(default_factory=list)
    mood: str = "balanced"
    pace: str = "normal"
    named_poi: str | None = None
    apply_realtime: bool = False
    days: int = 1
    start_name: str | None = None
    start_as_depot: bool = False
    start_hour: float | None = None
    # Fixed-time personal commitment (meeting / show / pickup…): geo + window.
    # prefer_near = venue/area/district; busy_*_hour = blocked sightseeing window.
    prefer_near: str | None = None
    busy_from_hour: float | None = None
    busy_until_hour: float | None = None
    # User-named sights the planner should try to include (Chinese short names).
    want_places: list[str] = field(default_factory=list)
    focus_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Stop:
    poi_id: str
    name: str
    category: str
    poi_type: str
    district: str
    dwell_minutes: float
    travel_to_next_minutes: float | None
    score: float
    reason: str
    completed: bool = False
    actual_dwell_minutes: float | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    realtime_status: dict[str, Any] = field(default_factory=dict)
    day_index: int = 1
    arrival_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RoutePlan:
    city: str
    mode: str
    scene: str
    stops: list[Stop]
    available_minutes: float
    safe_budget_minutes: float
    planned_minutes: float
    satisfaction_probability: float
    reminder_level: str
    voice: str
    realtime_notices: list[dict[str, Any]] = field(default_factory=list)
    route_reason: str = ""
    # Kept on the plan so edits can re-evaluate the chance constraint exactly
    # instead of back-deriving sigma from the budget.
    uncertainty_minutes: float = 30.0
    risk_epsilon: float = 0.1
    risk_model: str = "gaussian"
    chance_constraint_satisfied: bool = True
    day_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stops"] = [stop.to_dict() for stop in self.stops]
        return data
