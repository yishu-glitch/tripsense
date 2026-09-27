"""Route construction under a chance-constrained time budget.

Selecting which POIs to visit and in which order, to maximise collected score
subject to a time budget, is the Orienteering / Tourist Trip Design Problem and
is NP-hard, so a heuristic is required. The construction here is a beam search
(a width-limited best-first construction) followed by a 2-opt pass on the
ordering, which is the usual "construct then improve" pattern used for OP/TOPTW
(Vansteenwegen et al., *Iterated local search for the team orienteering problem
with time windows*, C&OR 2009). Beam width 1 reduces to the previous greedy
construction, so the search can only match or improve on it.

Feasibility is always checked against the deterministic budget returned by
:mod:`tripsense.core.constraints`, never against the raw available minutes.
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .constraints import (
    TimeBudget,
    chance_constraint_satisfied,
    completion_probability,
    time_budget,
)
from .dwell import estimate_dwell
from .models import Intent, RoutePlan, Stop
from .place_fallback import (
    alias_graph_node,
    build_synthetic_poi,
    city_pin,
    coords_in_city,
    district_centroid,
    geo_uncertainty_factor,
    lookup_place_prior,
    sanitize_supplement,
)
from .poi_supplement_store import record_poi_supplement
from .preference import ATTR_COLUMNS, MODE_WEIGHTS, PreferenceTracker
from .scoring import CORE_DISTRICTS, OUTER_DISTRICTS, score_records, is_theme_signature
from .intent import (
    is_descriptive_placeholder,
    utterance_switches_to_indoor,
    utterance_wants_landmarks,
)
from tripsense.knowledge.structured import parse_opening_hours

_NAME_PENALTY_MARKERS = ("专卖", "文创", "礼物店", "研学基地", "生活广场")

if TYPE_CHECKING:
    from tripsense.knowledge.retrieval import LayeredKnowledgeRetriever


DEFAULT_BEAM_WIDTH = 4
BEAM_CANDIDATE_LIMIT = 80
TWO_OPT_MAX_PASSES = 8
CLUSTER_TRAVEL_MAX = 32.0
CLUSTER_DISTANCE_KM = 6.0
MAX_HOP_MINUTES = 36.0
MAX_WINDOW_WAIT = 25.0
DEFAULT_START_HOUR = 10.0

OBJECTIVE_PROFILES = {
    "theme": {
        "travel_penalty": 0.028,
        "diversity_penalty": 0.12,
        "max_stops": 6,
        "weight_boost": np.array([1.25, 0.9, 0.7, 1.0, 0.85, 1.1]),
        "summary": "更贴主题与文化体验",
    },
    "easy": {
        "travel_penalty": 0.045,
        "diversity_penalty": 0.08,
        "max_stops": 4,
        "weight_boost": np.array([0.9, 1.0, 0.6, 0.85, 1.35, 0.8]),
        "summary": "少走、留白、偏室内从容",
    },
    "scenic": {
        "travel_penalty": 0.026,
        "diversity_penalty": 0.06,
        "max_stops": 6,
        "weight_boost": np.array([0.85, 1.15, 0.5, 1.45, 0.7, 0.75]),
        "summary": "景观与拍照表现更强",
    },
}


@dataclass(slots=True)
class _BeamState:
    order: tuple[str, ...]
    minutes: float
    utility: float
    clock: float
    type_counts: dict[str, int] = field(default_factory=dict)

    def extended(
        self,
        poi_id: str,
        poi_type: str,
        minutes: float,
        utility: float,
        clock: float,
    ) -> _BeamState:
        counts = dict(self.type_counts)
        counts[poi_type] = counts.get(poi_type, 0) + 1
        return _BeamState(
            order=(*self.order, poi_id),
            minutes=minutes,
            utility=utility,
            clock=clock,
            type_counts=counts,
        )


class RoutePlanner:
    def __init__(
        self,
        pois: pd.DataFrame,
        distances: dict,
        knowledge: LayeredKnowledgeRetriever | None = None,
        preference: PreferenceTracker | None = None,
        place_supplement=None,
        place_search=None,
        supplement_store_dir=None,
    ):
        self.pois = pois.copy()
        self.pois["poi_id"] = self.pois["poi_id"].astype(str)
        # Shallow-copy so ephemeral / alias edges do not leak across cities.
        self.distances = {
            str(origin): dict(edges) if isinstance(edges, dict) else edges
            for origin, edges in (distances or {}).items()
        }
        self.knowledge = knowledge
        self.preference = preference or PreferenceTracker()
        self.place_supplement = place_supplement
        self.place_search = place_search
        self.supplement_store_dir = supplement_store_dir
        self._ephemeral: dict[str, dict] = {}
        self._travel_aliases: dict[str, str] = {}
        self._coordinates = {
            str(row["poi_id"]): (float(row["gcj_lng"]), float(row["gcj_lat"]))
            for row in self.pois.to_dict("records")
            if pd.notna(row.get("gcj_lng")) and pd.notna(row.get("gcj_lat"))
        }
        self._travel_cache: dict[tuple[str, str], float] = {}
        self._geo_uncertainty: dict[str, float] = {}
        self._missing_place_notes: list[str] = []
        self._approx_place_notes: list[str] = []

    # ----------------------------------------------------------------- scoring

    def resolve_weights(
        self,
        intent: Intent,
        *,
        preference_weights: dict[str, float] | None = None,
        profile_id: str | None = None,
    ) -> np.ndarray:
        weights = self.preference.score_weights(intent.mode)
        if preference_weights:
            overlay = np.array(
                [
                    float(preference_weights.get(name, weights[i]))
                    for i, name in enumerate(ATTR_COLUMNS)
                ],
                dtype=float,
            )
            overlay = np.clip(overlay, 0.05, None)
            weights = 0.5 * weights + 0.5 * overlay
        if profile_id and profile_id in OBJECTIVE_PROFILES:
            weights = weights * OBJECTIVE_PROFILES[profile_id]["weight_boost"]
        return weights / max(float(weights.sum()), 1e-9) * float(MODE_WEIGHTS["balanced"].sum())

    def build_candidates(
        self,
        intent: Intent,
        top_n: int = 180,
        *,
        preference_weights: dict[str, float] | None = None,
        profile_id: str | None = None,
        prefer_categories: list[str] | None = None,
        avoid_outdoor: bool = False,
        exclude_poi_ids: set[str] | None = None,
    ) -> list[dict]:
        """Use the city's actual distance graph and the user's scene constraints."""
        weights = self.resolve_weights(
            intent, preference_weights=preference_weights, profile_id=profile_id
        )

        records: list[dict] = []
        if self.knowledge is not None and prefer_categories is None and not avoid_outdoor:
            retrieved = self.knowledge.retrieve(intent, limit=max(top_n, 180))
            records = [item.planner_record() for item in retrieved]
            if exclude_poi_ids:
                records = [row for row in records if str(row["poi_id"]) not in exclude_poi_ids]

        if not records:
            records = self._structured_pool(
                intent,
                prefer_categories=prefer_categories,
                avoid_outdoor=avoid_outdoor,
                exclude_poi_ids=exclude_poi_ids,
            )
        records = self._inject_want_places(intent, records, exclude_poi_ids=exclude_poi_ids)

        scored = score_records(records, intent, weights)
        scored = self._deduplicate_overlapping_names(scored)
        scored = self._deduplicate_parent_site_records(scored)
        return scored[:top_n]

    def _structured_pool(
        self,
        intent: Intent,
        *,
        prefer_categories: list[str] | None,
        avoid_outdoor: bool,
        exclude_poi_ids: set[str] | None,
    ) -> list[dict]:
        if "is_route_candidate" in self.pois.columns:
            route_mask = self.pois["is_route_candidate"].astype(str).str.lower().eq("true")
            frame = self.pois[route_mask].copy()
            if frame.empty:
                frame = self.pois.copy()
        else:
            graph_ids = set(map(str, self.distances.keys())) | set(self._travel_aliases)
            frame = self.pois[self.pois["poi_id"].isin(graph_ids)].copy()
        categories = prefer_categories or intent.categories
        want_ids = set(self._resolve_want_place_ids(intent))
        if categories:
            type_ok = frame["poi_type_en"].isin(categories)
            named_ok = frame["poi_id"].astype(str).isin(want_ids) if want_ids else False
            preferred = frame[type_ok | named_ok]
            if len(preferred) >= 2:
                frame = preferred
        if intent.excluded_categories:
            frame = frame[~frame["poi_type_en"].isin(intent.excluded_categories)]
        if avoid_outdoor and "indoor_score" in frame.columns:
            indoor = frame[frame["indoor_score"].fillna(0).astype(float) >= 0.55]
            if len(indoor) >= 2:
                frame = indoor
        if exclude_poi_ids:
            frame = frame[~frame["poi_id"].isin(exclude_poi_ids)]
        frame = self._deduplicate_parent_sites(frame)
        if frame.empty:
            return []
        return frame.to_dict("records")

    # ---------------------------------------------------------------- planning

    def plan(
        self,
        intent: Intent,
        *,
        max_stops: int = 5,
        preference_weights: dict[str, float] | None = None,
        profile_id: str | None = None,
        prefer_categories: list[str] | None = None,
        avoid_outdoor: bool = False,
        locked_stops: list[Stop] | None = None,
        exclude_poi_ids: set[str] | None = None,
        travel_penalty: float | None = None,
        diversity_penalty: float | None = None,
        beam_width: int = DEFAULT_BEAM_WIDTH,
    ) -> RoutePlan:
        profile = OBJECTIVE_PROFILES.get(profile_id or "", {})
        max_stops = int(profile.get("max_stops", max_stops))
        travel_penalty = float(
            travel_penalty if travel_penalty is not None else profile.get("travel_penalty", 0.028)
        )
        default_diversity = 0.04 if intent.scene in {"历史文化游", "城市漫游"} else 0.12
        diversity_penalty = float(
            diversity_penalty
            if diversity_penalty is not None
            else profile.get("diversity_penalty", default_diversity)
        )
        beam_width = max(1, int(beam_width))
        days = max(1, int(getattr(intent, "days", 1) or 1))
        # Full-day photo / landmark openers need room for multi-area spread.
        if days == 1 and intent.available_minutes >= 360 and (
            intent.mode in {"photo", "full"}
            or utterance_wants_landmarks(intent.utterance or "")
            or intent.scene in {"综合观光游", "历史文化游", "亲子研学游"}
        ):
            max_stops = max(max_stops, 6)
        if days == 1:
            return self._plan_one_day(
                intent,
                max_stops=max_stops,
                preference_weights=preference_weights,
                profile_id=profile_id,
                prefer_categories=prefer_categories,
                avoid_outdoor=avoid_outdoor,
                locked_stops=locked_stops,
                exclude_poi_ids=exclude_poi_ids,
                travel_penalty=travel_penalty,
                diversity_penalty=diversity_penalty,
                beam_width=beam_width,
                day_index=1,
            )

        excluded = set(exclude_poi_ids or [])
        day_plans: list[RoutePlan] = []
        for day_index in range(1, days + 1):
            day_locked = locked_stops if day_index == 1 else None
            day_plan = self._plan_one_day(
                intent,
                max_stops=max_stops,
                preference_weights=preference_weights,
                profile_id=profile_id,
                prefer_categories=prefer_categories,
                avoid_outdoor=avoid_outdoor,
                locked_stops=day_locked,
                exclude_poi_ids=excluded or None,
                travel_penalty=travel_penalty,
                diversity_penalty=diversity_penalty,
                beam_width=beam_width,
                day_index=day_index,
            )
            day_plans.append(day_plan)
            excluded.update(stop.poi_id for stop in day_plan.stops)

        return self._merge_day_plans(day_plans, intent)

    def _plan_one_day(
        self,
        intent: Intent,
        *,
        max_stops: int,
        preference_weights: dict[str, float] | None,
        profile_id: str | None,
        prefer_categories: list[str] | None,
        avoid_outdoor: bool,
        locked_stops: list[Stop] | None,
        exclude_poi_ids: set[str] | None,
        travel_penalty: float,
        diversity_penalty: float,
        beam_width: int,
        day_index: int,
    ) -> RoutePlan:
        candidates = self.build_candidates(
            intent,
            preference_weights=preference_weights,
            profile_id=profile_id,
            prefer_categories=prefer_categories,
            avoid_outdoor=avoid_outdoor,
            exclude_poi_ids=exclude_poi_ids,
        )
        if not candidates and not locked_stops:
            raise ValueError("no route candidates after applying city and scene filters")

        intent = self._with_commitment_timing(intent)

        budget_info = time_budget(
            intent.available_minutes,
            intent.uncertainty_minutes,
            intent.risk_epsilon,
            model=getattr(intent, "risk_model", "gaussian"),
            wasserstein_radius=getattr(intent, "wasserstein_radius", 0.0),
        )
        budget = budget_info.minutes
        start_clock = self._start_clock(intent)
        depot_id, visit_start = self._resolve_start(intent, candidates)
        near_id = self._resolve_named_place(getattr(intent, "prefer_near", None), candidates)
        want_ids = self._resolve_want_place_ids(intent)

        by_id: dict[str, dict] = {}
        locked_order: list[str] = []
        locked_minutes = 0.0
        blocked = set(exclude_poi_ids or [])
        for stop in locked_stops or []:
            row = self._stop_to_candidate(stop)
            poi_id = str(row["poi_id"])
            if poi_id in blocked:
                continue
            travel = 0.0 if not locked_order else self._travel(locked_order[-1], poi_id)
            if not locked_order and depot_id and not visit_start:
                travel = self._travel(depot_id, poi_id)
            needed = travel + float(stop.dwell_minutes)
            if locked_order and locked_minutes + needed > budget:
                break
            by_id[poi_id] = row
            locked_order.append(poi_id)
            locked_minutes += needed

        if visit_start and depot_id and depot_id not in by_id and depot_id not in blocked:
            seed = next((row for row in candidates if str(row["poi_id"]) == depot_id), None)
            if seed is not None:
                by_id[depot_id] = dict(seed)
                locked_order = [depot_id, *locked_order]

        # Soft-seeded / user want_places must not reappear on later multi-day legs.
        active_wants = [poi_id for poi_id in want_ids if poi_id not in blocked]
        for poi_id in active_wants:
            if poi_id in by_id:
                continue
            extra = self._row_by_id(poi_id)
            if extra is not None:
                by_id[poi_id] = extra

        last_locked = locked_order[-1] if locked_order else depot_id
        for poi_id in active_wants:
            if poi_id in locked_order or poi_id not in by_id:
                continue
            travel = 0.0 if last_locked is None else self._travel(last_locked, poi_id)
            needed = travel + self._dwell_for(by_id[poi_id], intent)
            if locked_order and locked_minutes + needed > budget:
                break
            locked_order.append(poi_id)
            locked_minutes += needed
            last_locked = poi_id

        pool_rows = [row for row in candidates if str(row["poi_id"]) not in by_id]
        for row in pool_rows:
            by_id[str(row["poi_id"])] = dict(row)
        # Day 1 keeps geographic density. Later days seed from the strongest
        # remaining theme signature (e.g. 武康路 after a Huangpu day) so the
        # itinerary still matches user mental models without breaking day-1 geo.
        seed_id = locked_order[0] if locked_order else depot_id
        if seed_id is None and near_id and near_id in by_id:
            seed_id = near_id
            busy_from = getattr(intent, "busy_from_hour", None)
            # Early/midday commitments: start near the area. Late ones rely on
            # scoring + beam end-pull so the day converges toward the place.
            if (busy_from is None or float(busy_from) < 16.0) and near_id not in locked_order:
                locked_order = [near_id, *locked_order]
        elif seed_id is None and day_index > 1:
            seed_id = self._theme_seed_id(pool_rows, intent)
            # Soft-lock the signature as day-N's first stop so the day reads as
            # "武康路片区" rather than a nearby temple that happens to score high.
            if seed_id and seed_id in by_id and seed_id not in locked_order:
                locked_order = [seed_id, *locked_order]
        elif seed_id is None:
            # Avoid a high-scoring but hop-isolated seed (e.g. suburban memorial)
            # that traps beam search at 1–2 stops on a full-day budget.
            seed_id = self._density_aware_seed_id(pool_rows, max_stops=max_stops)
        pool = self._coherent_pool(
            seed_id=seed_id or (locked_order[0] if locked_order else None),
            ranked=([by_id[pid] for pid in locked_order] + pool_rows),
            by_id=by_id,
        )
        pool = [pid for pid in pool if pid not in set(locked_order)][:BEAM_CANDIDATE_LIMIT]

        order = self._beam_search(
            locked_order=locked_order,
            pool=pool,
            by_id=by_id,
            intent=intent,
            budget=budget,
            max_stops=max_stops,
            beam_width=beam_width,
            travel_penalty=travel_penalty,
            diversity_penalty=diversity_penalty,
            start_clock=start_clock,
            depot_id=None if visit_start else depot_id,
            prefer_near_id=near_id,
        )
        improved = self._two_opt(order, by_id, intent, budget, len(locked_order))
        if self._windows_feasible(improved, by_id, intent, start_clock, None if visit_start else depot_id):
            order = improved

        selected: list[dict] = []
        clock = start_clock
        last_id = None if visit_start else depot_id
        for poi_id in order:
            row = dict(by_id[poi_id])
            travel = 0.0 if last_id is None else self._travel(last_id, poi_id)
            wait, _ok = self._window_wait(row, clock + travel)
            row["_incoming_travel"] = travel
            row["_arrival_clock"] = clock + travel + wait
            selected.append(row)
            clock = row["_arrival_clock"] + self._dwell_for(row, intent)
            last_id = poi_id

        selected = self._collapse_parent_site_stops(selected)
        return self._selected_to_plan(
            selected,
            intent,
            budget_info,
            profile_id=profile_id,
            day_index=day_index,
            day_count=max(1, int(getattr(intent, "days", 1) or 1)),
        )

    def _merge_day_plans(self, day_plans: list[RoutePlan], intent: Intent) -> RoutePlan:
        stops: list[Stop] = []
        for plan in day_plans:
            stops.extend(plan.stops)
        if not stops:
            raise ValueError("no route candidates after applying city and scene filters")
        planned = sum(plan.planned_minutes for plan in day_plans)
        available = float(intent.available_minutes) * len(day_plans)
        safe = sum(plan.safe_budget_minutes for plan in day_plans)
        reminder, voice = self._reminder(planned, available)
        return RoutePlan(
            city=intent.city,
            mode=intent.mode,
            scene=intent.scene,
            stops=stops,
            available_minutes=available,
            safe_budget_minutes=round(safe, 1),
            planned_minutes=round(planned, 1),
            satisfaction_probability=min(plan.satisfaction_probability for plan in day_plans),
            reminder_level=reminder,
            voice=voice,
            realtime_notices=day_plans[0].realtime_notices,
            route_reason=day_plans[0].route_reason or voice,
            uncertainty_minutes=float(intent.uncertainty_minutes),
            risk_epsilon=float(intent.risk_epsilon),
            risk_model=day_plans[0].risk_model,
            chance_constraint_satisfied=all(plan.chance_constraint_satisfied for plan in day_plans),
            day_count=len(day_plans),
        )

    def _beam_search(
        self,
        *,
        locked_order: list[str],
        pool: list[str],
        by_id: dict[str, dict],
        intent: Intent,
        budget: float,
        max_stops: int,
        beam_width: int,
        travel_penalty: float,
        diversity_penalty: float,
        start_clock: float = DEFAULT_START_HOUR * 60,
        depot_id: str | None = None,
        prefer_near_id: str | None = None,
    ) -> list[str]:
        start = _BeamState(order=(), minutes=0.0, utility=0.0, clock=start_clock)
        for index, poi_id in enumerate(locked_order):
            row = by_id[poi_id]
            last = locked_order[index - 1] if index else depot_id
            travel = 0.0 if last is None else self._travel(last, poi_id)
            wait, feasible = self._window_wait(row, start.clock + travel)
            dwell = self._dwell_for(row, intent)
            start = start.extended(
                poi_id,
                str(row.get("poi_type_en", "")),
                start.minutes + travel + wait + dwell,
                start.utility + float(row.get("_score", 0.0)) - travel_penalty * travel,
                start.clock + travel + wait + dwell if feasible else start.clock + travel + dwell,
            )

        beam = [start]
        best = start
        for _ in range(max(0, max_stops - len(locked_order))):
            expansions: list[_BeamState] = []
            for state in beam:
                taken = set(state.order)
                last = state.order[-1] if state.order else depot_id
                progress = len(state.order) / max(1, max_stops)
                for poi_id in pool:
                    if poi_id in taken:
                        continue
                    row = by_id[poi_id]
                    travel = 0.0 if last is None else self._travel(last, poi_id)
                    if last is not None and travel > MAX_HOP_MINUTES:
                        continue
                    wait, feasible = self._window_wait(row, state.clock + travel)
                    if not feasible:
                        continue
                    dwell = self._dwell_for(row, intent)
                    minutes = state.minutes + travel + wait + dwell
                    if minutes > budget:
                        continue
                    poi_type = str(row.get("poi_type_en", ""))
                    repeats = state.type_counts.get(poi_type, 0)
                    parent_key = str(row.get("parent_site") or "").strip() or str(
                        row.get("poi_id")
                    )
                    parent_repeats = 0
                    for prior_id in state.order:
                        prior = by_id.get(prior_id) or {}
                        prior_key = str(prior.get("parent_site") or "").strip() or prior_id
                        if prior_key == parent_key:
                            parent_repeats += 1
                    # Photo / full-day / landmark: never pack one park's children.
                    spread = intent.mode in {"photo", "full"} or intent.available_minutes >= 360
                    parent_penalty = 0.42 if spread else 0.18
                    utility = (
                        state.utility
                        + float(row.get("_score", 0.0))
                        - travel_penalty * (travel + wait)
                        - diversity_penalty * repeats
                        - parent_penalty * parent_repeats
                    )
                    if prefer_near_id:
                        # Later stops pull harder toward the commitment area.
                        hop = self._travel(poi_id, prefer_near_id)
                        utility += max(0.0, 0.22 - 0.004 * hop) * (0.35 + 0.65 * progress)
                    expansions.append(
                        state.extended(
                            poi_id,
                            poi_type,
                            minutes,
                            utility,
                            state.clock + travel + wait + dwell,
                        )
                    )
            if not expansions:
                break
            expansions.sort(key=lambda item: (-item.utility, item.minutes, item.order))
            beam = expansions[:beam_width]
            if beam[0].utility > best.utility:
                best = beam[0]
        return list(best.order)

    def _two_opt(
        self,
        order: list[str],
        by_id: dict[str, dict],
        intent: Intent,
        budget: float,
        locked_count: int,
    ) -> list[str]:
        """Reverse sub-paths while travel strictly drops and the budget holds."""
        if len(order) < 3:
            return order
        best = list(order)
        best_travel = self._path_travel(best)
        dwell_total = sum(self._dwell_for(by_id[pid], intent) for pid in best)
        for _ in range(TWO_OPT_MAX_PASSES):
            improved = False
            for i in range(max(locked_count, 0), len(best) - 1):
                for j in range(i + 1, len(best)):
                    candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                    travel = self._path_travel(candidate)
                    if travel + 1e-9 >= best_travel:
                        continue
                    if dwell_total + travel > budget + 1e-9:
                        continue
                    best, best_travel, improved = candidate, travel, True
            if not improved:
                break
        return best

    def plan_alternatives(
        self,
        intent: Intent,
        *,
        preference_weights: dict[str, float] | None = None,
        prefer_categories: list[str] | None = None,
        avoid_outdoor: bool = False,
        profiles: tuple[str, ...] = ("theme", "easy", "scenic"),
        jaccard_max: float = 0.7,
    ) -> list[tuple[str, RoutePlan, str]]:
        """Generate mutually distinct feasible plans under different objective profiles."""
        alternatives: list[tuple[str, RoutePlan, str]] = []
        excluded: set[str] = set()
        for profile_id in profiles:
            try:
                plan = self.plan(
                    intent,
                    preference_weights=preference_weights,
                    profile_id=profile_id,
                    prefer_categories=prefer_categories,
                    avoid_outdoor=avoid_outdoor,
                    exclude_poi_ids=excluded or None,
                )
            except ValueError:
                continue
            ids = {stop.poi_id for stop in plan.stops}
            if alternatives:
                best_overlap = max(
                    self._jaccard(ids, {stop.poi_id for stop in existing.stops})
                    for _, existing, _ in alternatives
                )
                if best_overlap > jaccard_max and plan.stops:
                    seed = plan.stops[0].poi_id
                    try:
                        plan = self.plan(
                            intent,
                            preference_weights=preference_weights,
                            profile_id=profile_id,
                            prefer_categories=prefer_categories,
                            avoid_outdoor=avoid_outdoor,
                            exclude_poi_ids=(excluded | {seed}) or None,
                        )
                    except ValueError:
                        continue
                    ids = {stop.poi_id for stop in plan.stops}
                    best_overlap = max(
                        self._jaccard(ids, {stop.poi_id for stop in existing.stops})
                        for _, existing, _ in alternatives
                    )
                    if best_overlap > jaccard_max:
                        continue
            summary = str(OBJECTIVE_PROFILES[profile_id]["summary"])
            alternatives.append((profile_id, plan, summary))
            if plan.stops:
                excluded.add(plan.stops[0].poi_id)
        if not alternatives:
            plan = self.plan(
                intent,
                preference_weights=preference_weights,
                prefer_categories=prefer_categories,
                avoid_outdoor=avoid_outdoor,
            )
            alternatives.append(("theme", plan, "默认可行路线"))
        return alternatives

    def replan(
        self,
        intent: Intent,
        current: RoutePlan,
        ops: list[dict],
        *,
        preference_weights: dict[str, float] | None = None,
    ) -> RoutePlan:
        """Apply validated PlanOps and re-optimize unlocked segments under CCP budget."""
        locked_ids: set[str] = set()
        remove_ids: set[str] = set()
        prefer_categories: list[str] | None = None
        avoid_outdoor = False
        scope = "all"

        for raw in ops:
            if not isinstance(raw, dict):
                continue
            op = str(raw.get("op", ""))
            if op == "lock":
                for poi_id in raw.get("poi_ids") or []:
                    locked_ids.add(str(poi_id))
            elif op == "remove":
                if raw.get("poi_id"):
                    remove_ids.add(str(raw["poi_id"]))
            elif op == "prefer_categories":
                cats = [
                    str(item)
                    for item in (raw.get("categories") or [])
                    if item in {"attraction", "heritage", "park", "museum", "culture", "leisure"}
                ]
                if cats:
                    prefer_categories = cats
            elif op == "avoid_outdoor":
                avoid_outdoor = bool(raw.get("enabled"))
            elif op == "replan":
                scope = str(raw.get("scope") or "all")

        current_ids = {stop.poi_id for stop in current.stops}
        locked_ids &= current_ids
        remove_ids &= current_ids
        locked_ids -= remove_ids

        if scope == "tail" and current.stops:
            keep = current.stops[: max(1, len(current.stops) // 2)]
            locked_ids |= {stop.poi_id for stop in keep} - remove_ids

        locked_stops = [stop for stop in current.stops if stop.poi_id in locked_ids]
        return self.plan(
            intent,
            preference_weights=preference_weights,
            prefer_categories=prefer_categories,
            avoid_outdoor=bool(avoid_outdoor)
            and (
                bool(intent.apply_realtime)
                or utterance_switches_to_indoor(getattr(intent, "utterance", "") or "")
            ),
            locked_stops=locked_stops,
            exclude_poi_ids=remove_ids,
        )

    def _selected_to_plan(
        self,
        selected: list[dict],
        intent: Intent,
        budget_info: TimeBudget,
        *,
        profile_id: str | None = None,
        day_index: int = 1,
        day_count: int = 1,
    ) -> RoutePlan:
        stops: list[Stop] = []
        for index, poi in enumerate(selected):
            next_travel = None
            if index + 1 < len(selected):
                next_travel = selected[index + 1].get("_incoming_travel")
                if next_travel is None:
                    next_travel = self._travel(
                        str(poi["poi_id"]), str(selected[index + 1]["poi_id"])
                    )
            arrival = poi.get("_arrival_clock")
            stops.append(
                Stop(
                    poi_id=str(poi["poi_id"]),
                    name=str(poi["name"]),
                    category=str(poi.get("category_name", poi.get("category", ""))),
                    poi_type=str(poi.get("poi_type_en", poi.get("poi_type", ""))),
                    district=str(poi.get("district", "")),
                    dwell_minutes=self._dwell_for(poi, intent),
                    travel_to_next_minutes=(
                        round(float(next_travel), 1) if next_travel is not None else None
                    ),
                    score=round(float(poi.get("_score", poi.get("score", 0.0))), 4),
                    reason=self._reason(poi, intent),
                    evidence=list(poi.get("_knowledge_evidence", poi.get("evidence", []))),
                    realtime_status=dict(
                        poi.get("_realtime_status", poi.get("realtime_status", {}))
                    ),
                    day_index=day_index,
                    arrival_time=self._format_clock(arrival) if arrival is not None else None,
                )
            )

        planned = self.total_minutes(stops)
        reminder, voice = self._reminder(planned, intent.available_minutes)
        realtime_notices = self._collect_realtime_notices(selected)
        route_reason = voice
        if profile_id and profile_id in OBJECTIVE_PROFILES:
            route_reason = str(OBJECTIVE_PROFILES[profile_id]["summary"])
        return RoutePlan(
            city=intent.city,
            mode=intent.mode,
            scene=intent.scene,
            stops=stops,
            available_minutes=float(intent.available_minutes),
            safe_budget_minutes=budget_info.minutes,
            planned_minutes=round(planned, 1),
            satisfaction_probability=completion_probability(
                planned, intent.available_minutes, intent.uncertainty_minutes
            ),
            reminder_level=reminder,
            voice=voice,
            realtime_notices=realtime_notices,
            route_reason=route_reason,
            uncertainty_minutes=float(intent.uncertainty_minutes),
            risk_epsilon=float(intent.risk_epsilon),
            risk_model=budget_info.model,
            chance_constraint_satisfied=chance_constraint_satisfied(
                planned,
                intent.available_minutes,
                intent.uncertainty_minutes,
                intent.risk_epsilon,
            ),
            day_count=day_count,
        )

    def _stop_to_candidate(self, stop: Stop) -> dict:
        row = self.pois[self.pois["poi_id"] == stop.poi_id]
        if not row.empty:
            record = row.iloc[0].to_dict()
        else:
            record = {
                "poi_id": stop.poi_id,
                "name": stop.name,
                "category_name": stop.category,
                "poi_type_en": stop.poi_type,
                "district": stop.district,
                "evidence": stop.evidence,
                "realtime_status": stop.realtime_status,
            }
        record["_score"] = float(stop.score)
        # A locked stop keeps the dwell the user already accepted, so budget
        # accounting and the emitted plan cannot disagree.
        record["_locked_dwell"] = float(stop.dwell_minutes)
        return record

    def normalize(self, plan: RoutePlan) -> RoutePlan:
        """Recalculate every adjacent edge and all derived route fields after an edit."""
        stops = [replace(stop) for stop in plan.stops]
        for index, stop in enumerate(stops):
            stop.travel_to_next_minutes = (
                self._travel(stop.poi_id, stops[index + 1].poi_id)
                if index + 1 < len(stops)
                else None
            )
        planned = self.total_minutes(stops)
        reminder, voice = self._reminder(planned, plan.available_minutes)
        return replace(
            plan,
            stops=stops,
            planned_minutes=round(planned, 1),
            satisfaction_probability=completion_probability(
                planned, plan.available_minutes, plan.uncertainty_minutes
            ),
            reminder_level=reminder,
            voice=voice,
            chance_constraint_satisfied=chance_constraint_satisfied(
                planned, plan.available_minutes, plan.uncertainty_minutes, plan.risk_epsilon
            ),
        )

    # ----------------------------------------------------------------- travel

    def _travel(self, origin: str, destination: str) -> float:
        if str(origin) == str(destination):
            return 0.0
        key = (str(origin), str(destination))
        cached = self._travel_cache.get(key)
        if cached is None:
            cached = self.travel_minutes(origin, destination)
            self._travel_cache[key] = cached
        return cached

    def _path_travel(self, order: list[str]) -> float:
        return sum(
            self._travel(order[index], order[index + 1]) for index in range(len(order) - 1)
        )

    def travel_minutes(self, origin: str, destination: str) -> float:
        raw_origin, raw_dest = str(origin), str(destination)
        origin = self._travel_aliases.get(raw_origin, raw_origin)
        destination = self._travel_aliases.get(raw_dest, raw_dest)
        direct = self.distances.get(str(origin), {}).get(str(destination))
        if isinstance(direct, dict):
            value = direct.get("travel_min", direct.get("walk_min"))
            if value is not None:
                return round(float(value), 1)
        estimated = self._shortest_path(str(origin), str(destination))
        if estimated is not None:
            return round(estimated, 1)
        geographic = self._geographic_travel_estimate(str(origin), str(destination))
        base = geographic if geographic is not None else 45.0
        return round(base * self._travel_uncertainty(raw_origin, raw_dest), 1)

    def _travel_uncertainty(self, origin: str, destination: str) -> float:
        return max(
            float(self._geo_uncertainty.get(str(origin), 1.0)),
            float(self._geo_uncertainty.get(str(destination), 1.0)),
            1.0,
        )

    def _geographic_travel_estimate(self, origin: str, destination: str) -> float | None:
        if origin not in self._coordinates or destination not in self._coordinates:
            return None
        lng1, lat1 = self._coordinates[origin]
        lng2, lat2 = self._coordinates[destination]
        radius_km = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)
        value = (
            math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        straight_km = radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
        if straight_km <= 1.2:
            return max(5.0, straight_km * 1.25 / 4.5 * 60)
        if straight_km <= 8.0:
            return 8.0 + straight_km / 22.0 * 60
        return 15.0 + straight_km / 18.0 * 60

    def _shortest_path(self, origin: str, destination: str) -> float | None:
        if origin not in self.distances:
            return None
        queue: list[tuple[float, str]] = [(0.0, origin)]
        best = {origin: 0.0}
        while queue:
            cost, current = heapq.heappop(queue)
            if current == destination:
                return cost
            if cost > best.get(current, float("inf")):
                continue
            for neighbor, edge in self.distances.get(current, {}).items():
                weight = edge.get("travel_min", edge.get("walk_min"))
                if weight is None:
                    continue
                new_cost = cost + float(weight)
                if new_cost < best.get(str(neighbor), float("inf")):
                    best[str(neighbor)] = new_cost
                    heapq.heappush(queue, (new_cost, str(neighbor)))
        return None

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def total_minutes(stops: list[Stop]) -> float:
        return sum(stop.dwell_minutes + (stop.travel_to_next_minutes or 0.0) for stop in stops)

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 1.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)

    _CHILD_NAME_SUFFIXES = ("北里", "南里", "东区", "西区", "分馆", "分店", "店", "景区", "园区")

    @classmethod
    def _names_are_parent_child(cls, left: str, right: str) -> bool:
        if len(left) < 4 or len(right) < 4:
            return False
        longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
        if not longer.startswith(shorter):
            return False
        remainder = longer[len(shorter) :].strip("-—·（() ")
        if not remainder:
            return True
        # 天坛公园-祈年殿 / 故宫博物院-养心殿 style children
        if longer[len(shorter) : len(shorter) + 1] in {"-", "—", "·"}:
            return True
        return any(
            remainder == suffix or remainder.startswith(suffix) for suffix in cls._CHILD_NAME_SUFFIXES
        )

    @classmethod
    def _deduplicate_overlapping_names(cls, records: list[dict]) -> list[dict]:
        """Drop child/alias rows whose name extends a higher-ranked sibling."""
        kept: list[dict] = []
        accepted: list[tuple[str, str]] = []
        for row in records:
            name = str(row.get("name", "")).strip()
            district = str(row.get("district", ""))
            skip = any(
                district == other_district and cls._names_are_parent_child(name, other)
                for other, other_district in accepted
            )
            if not skip:
                kept.append(row)
                accepted.append((name, district))
        return kept

    @staticmethod
    def _parent_site_key(row: dict) -> str:
        parent = str(row.get("parent_site") or "").strip()
        if parent and parent.lower() not in {"nan", "none"}:
            return parent
        name = str(row.get("name") or "").strip()
        for sep in ("-", "—", "·"):
            if sep in name:
                return name.split(sep, 1)[0].strip()
        return ""

    def _catalog_main_for_parent(self, parent_key: str) -> dict | None:
        if not parent_key or "parent_site" not in self.pois.columns:
            return None
        frame = self.pois[
            (self.pois["parent_site"].fillna("").astype(str) == parent_key)
            & (self.pois["is_main_site"].astype(str).str.lower().isin(["true", "1"]))
        ]
        if frame.empty:
            return None
        return frame.iloc[0].to_dict()

    def _deduplicate_parent_site_records(self, records: list[dict]) -> list[dict]:
        """Keep one POI per parent_site — prefer catalog main, then score."""
        if not records:
            return records
        best: dict[str, dict] = {}
        for row in records:
            key = self._parent_site_key(row)
            if not key:
                continue
            current = best.get(key)
            if current is None:
                best[key] = row
                continue
            row_main = str(row.get("is_main_site", "")).lower() in {"true", "1"}
            cur_main = str(current.get("is_main_site", "")).lower() in {"true", "1"}
            if row_main and not cur_main:
                best[key] = row
                continue
            if cur_main and not row_main:
                continue
            row_score = float(row.get("_score", row.get("popularity", 0) or 0) or 0)
            cur_score = float(current.get("_score", current.get("popularity", 0) or 0) or 0)
            if row_score > cur_score:
                best[key] = row
        for key, row in list(best.items()):
            if str(row.get("is_main_site", "")).lower() in {"true", "1"}:
                continue
            main = self._catalog_main_for_parent(key)
            if main is not None:
                main = dict(main)
                main["_score"] = float(row.get("_score", main.get("popularity", 0) or 0) or 0)
                best[key] = main
        winners = {str(row.get("poi_id")) for row in best.values()}
        ordered: list[dict] = []
        seen: set[str] = set()
        for row in records:
            poi_id = str(row.get("poi_id"))
            key = self._parent_site_key(row)
            if not key:
                if poi_id not in seen:
                    seen.add(poi_id)
                    ordered.append(row)
                continue
            chosen = best.get(key)
            if chosen is None:
                continue
            chosen_id = str(chosen.get("poi_id"))
            if chosen_id in seen:
                continue
            if poi_id in winners or chosen_id not in seen:
                seen.add(chosen_id)
                ordered.append(chosen)
        return ordered

    def _collapse_parent_site_stops(self, selected: list[dict]) -> list[dict]:
        """Emit at most one stop per parent_site (e.g. 天坛公园 not +祈年殿)."""
        if len(selected) < 2:
            return selected
        kept: list[dict] = []
        seen_parents: set[str] = set()
        for row in selected:
            key = self._parent_site_key(row)
            if key and key in seen_parents:
                continue
            if key:
                main = self._catalog_main_for_parent(key)
                if main is not None and str(main.get("poi_id")) != str(row.get("poi_id")):
                    upgraded = dict(main)
                    upgraded["_arrival_clock"] = row.get("_arrival_clock")
                    upgraded["_incoming_travel"] = row.get("_incoming_travel")
                    upgraded["_score"] = row.get("_score", main.get("popularity", 0))
                    row = upgraded
                for index, prior in enumerate(kept):
                    if self._parent_site_key(prior) == key:
                        kept[index] = row
                        break
                else:
                    kept.append(row)
                seen_parents.add(key)
                continue
            kept.append(row)
        return kept

    @staticmethod
    def _deduplicate_parent_sites(frame: pd.DataFrame) -> pd.DataFrame:
        parent_col = "parent_site" if "parent_site" in frame.columns else (
            "site_parent_id" if "site_parent_id" in frame.columns else None
        )
        if parent_col is None:
            return frame
        parent = frame[parent_col].fillna("").astype(str)
        root = frame[parent.isin(["", "nan"])]
        children = frame[~parent.isin(["", "nan"])].copy()
        if children.empty:
            return frame
        children["_main"] = children["is_main_site"].astype(str).isin(["True", "true", "1"])
        children = children.sort_values(["_main", "popularity"], ascending=False)
        children = children.drop_duplicates(parent_col, keep="first").drop(columns="_main")
        return pd.concat([root, children], ignore_index=True)

    def _dwell_for(self, poi: dict, intent: Intent) -> float:
        locked = poi.get("_locked_dwell")
        if locked is not None:
            return float(locked)
        return self._dwell_minutes(poi, intent)

    @staticmethod
    def _dwell_minutes(poi: dict, intent: Intent) -> float:
        return estimate_dwell(poi, pace=intent.pace)

    @staticmethod
    def _start_clock(intent: Intent) -> float:
        hour = intent.start_hour if intent.start_hour is not None else DEFAULT_START_HOUR
        return float(hour) * 60.0

    @staticmethod
    def _with_commitment_timing(intent: Intent) -> Intent:
        """Apply fixed-commitment windows as start/budget soft constraints."""
        kwargs: dict = {}
        busy_from = getattr(intent, "busy_from_hour", None)
        busy_until = getattr(intent, "busy_until_hour", None)
        if busy_until is not None and intent.start_hour is None and (
            busy_from is None or float(busy_until) <= float(busy_from) + 0.1
        ):
            # Commitment already finished / single point — resume sightseeing after.
            if busy_from is not None and float(busy_from) <= 12.5:
                kwargs["start_hour"] = float(busy_until)
        start_h = float(
            kwargs.get("start_hour", intent.start_hour if intent.start_hour is not None else DEFAULT_START_HOUR)
        )
        if busy_from is not None:
            # Leave ~45 minutes buffer before the commitment.
            minutes_until = (float(busy_from) - 0.75 - start_h) * 60.0
            if minutes_until >= 90:
                kwargs["available_minutes"] = min(int(intent.available_minutes), int(minutes_until))
            elif intent.start_hour is None and busy_until is not None:
                kwargs["start_hour"] = float(busy_until)
        return replace(intent, **kwargs) if kwargs else intent

    def _inject_want_places(
        self,
        intent: Intent,
        records: list[dict],
        *,
        exclude_poi_ids: set[str] | None = None,
    ) -> list[dict]:
        blocked = set(exclude_poi_ids or [])
        have = {str(row.get("poi_id")) for row in records}
        extra: list[dict] = []
        for poi_id in self._resolve_want_place_ids(intent):
            if poi_id in have or poi_id in blocked:
                continue
            row = self._row_by_id(poi_id)
            if row is None:
                continue
            extra.append(row)
            have.add(poi_id)
        return extra + records

    def _row_by_id(self, poi_id: str) -> dict | None:
        if str(poi_id) in self._ephemeral:
            return dict(self._ephemeral[str(poi_id)])
        rows = self.pois[self.pois["poi_id"].astype(str) == str(poi_id)]
        if rows.empty:
            return None
        return rows.iloc[0].to_dict()

    def _resolve_want_place_ids(self, intent: Intent) -> list[str]:
        ids: list[str] = []
        for name in getattr(intent, "want_places", None) or []:
            poi_id = self._resolve_named_place(name, kind="place")
            if not poi_id:
                # Soft pass: parent/sibling / district lexical before inventing coords.
                poi_id = self._resolve_named_place(name, kind="area")
            if not poi_id:
                extra = self._supplement_named_place(name, intent)
                poi_id = str(extra["poi_id"]) if extra else None
            if poi_id and poi_id not in ids:
                ids.append(poi_id)
        return ids

    @staticmethod
    def _city_key(row: dict) -> str:
        city = str(row.get("city", ""))
        if city == "shanghai" or "上海" in city:
            return "shanghai"
        return "beijing"

    def _named_place_score(self, label: str, row: dict, *, kind: str) -> float:
        name = str(row.get("name", ""))
        district = str(row.get("district", ""))
        address = str(row.get("address", ""))
        parent = str(row.get("parent_site") or "").strip()
        poi_type = str(row.get("poi_type_en", row.get("poi_type", "")))
        if any(marker in name for marker in _NAME_PENALTY_MARKERS):
            return -1.0
        if poi_type == "leisure" and kind == "place":
            return -1.0
        main = str(row.get("is_main_site", "")).lower() in {"true", "1"}
        parent_hit = bool(
            parent
            and (
                label == parent
                or parent.startswith(label)
                or label.startswith(parent)
            )
        )
        score = -1.0
        if label == name:
            score = 100.0
        elif parent_hit:
            score = 92.0 if main else 70.0
            if label in name or name.startswith(label):
                score += 6.0
        elif name.startswith(label) and "-" not in name and "—" not in name:
            score = 82.0
        elif label in name:
            score = 28.0 if ("-" in name or "—" in name) else 64.0
        elif len(name) >= 3 and name in label:
            score = 50.0
        elif kind == "area" and (label in district or label in address):
            score = 36.0
        if score < 0:
            return score
        if main:
            score += 15.0
        try:
            score += min(10.0, float(row.get("popularity", 0) or 0) * 10.0)
        except (TypeError, ValueError):
            pass
        city = self._city_key(row)
        if district in CORE_DISTRICTS.get(city, set()):
            score += 5.0
        elif district in OUTER_DISTRICTS.get(city, set()):
            score -= 50.0
        return score

    def _supplement_named_place(self, name: str, intent: Intent) -> dict | None:
        """Fill a catalog miss: prefer real geo, else soft-pin so the named place stays in plan."""
        from .intent import sanitize_want_places

        if is_descriptive_placeholder(name):
            return None
        if not sanitize_want_places([name]):
            return None
        city = intent.city
        prior = lookup_place_prior(name, city)
        searched = None
        if self.place_search is not None:
            try:
                searched = sanitize_supplement(
                    self.place_search(name, city, intent),
                    name=name,
                    city=city,
                )
            except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
                searched = None
        llm_extra = None
        # Prefer Amap (or other search) geo; LLM may refine labels / supply coords if search misses.
        if self.place_supplement is not None:
            try:
                llm_extra = sanitize_supplement(
                    self.place_supplement(name, city, intent),
                    name=name,
                    city=city,
                )
            except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
                llm_extra = None

        sources: list[str] = []
        evidence: list[str] = []
        merged: dict = {}
        if prior:
            merged.update(prior)
            sources.append("city_prior")
        if searched:
            for key in ("name", "district", "poi_type_en", "category_name", "reason", "source"):
                if searched.get(key):
                    merged[key] = searched[key]
            if searched.get("gcj_lng") is not None and searched.get("gcj_lat") is not None:
                if coords_in_city(city, float(searched["gcj_lng"]), float(searched["gcj_lat"])):
                    merged["gcj_lng"] = searched["gcj_lng"]
                    merged["gcj_lat"] = searched["gcj_lat"]
                else:
                    # Cross-city hit (e.g. Langfang) — drop geo, soft-pin in-city later.
                    sources.append("amap_out_of_city")
            sources.append(str(searched.get("source") or "amap"))
            raw_evidence = searched.get("evidence")
            if isinstance(raw_evidence, list):
                evidence.extend(str(item) for item in raw_evidence[:5])
        if llm_extra:
            for key in ("name", "district", "poi_type_en", "category_name", "reason"):
                if llm_extra.get(key):
                    merged[key] = llm_extra[key]
            if (
                (merged.get("gcj_lng") is None or merged.get("gcj_lat") is None)
                and llm_extra.get("gcj_lng") is not None
                and llm_extra.get("gcj_lat") is not None
            ):
                if coords_in_city(city, float(llm_extra["gcj_lng"]), float(llm_extra["gcj_lat"])):
                    merged["gcj_lng"] = llm_extra["gcj_lng"]
                    merged["gcj_lat"] = llm_extra["gcj_lat"]
                    sources.append("llm_web")
                else:
                    sources.append("llm_out_of_city")
            elif llm_extra:
                sources.append("llm_label")

        display = str(merged.get("name") or name).strip()
        if is_descriptive_placeholder(display):
            return None
        lng = merged.get("gcj_lng")
        lat = merged.get("gcj_lat")
        district = str(merged.get("district") or "")
        # Reject foreign-city districts that slipped past citylimit.
        if district and any(
            marker in district
            for marker in ("廊坊", "天津", "保定", "张家口", "承德", "苏州", "嘉兴", "昆山")
        ):
            district = ""
            lng, lat = None, None
        category = str(merged.get("poi_type_en") or "attraction")
        searched_lng = lng
        searched_lat = lat
        geo_quality = "exact"
        travel_alias = None

        if lng is not None and lat is not None and not coords_in_city(city, float(lng), float(lat)):
            lng, lat = None, None

        if lng is None or lat is None:
            soft = self._soft_geo_for_supplement(
                name=display,
                city=city,
                district=district,
                category=category,
                intent=intent,
            )
            lng = soft["gcj_lng"]
            lat = soft["gcj_lat"]
            if not district and soft.get("district"):
                district = str(soft["district"])
            geo_quality = str(soft.get("geo_quality") or "city_prior")
            travel_alias = soft.get("travel_alias")
            sources.append(f"soft:{geo_quality}")
            approx_note = (
                f"公开检索没拿到「{name}」的精确坐标，先按区划/邻近已确认地点排一版，入库后再校正"
            )
            if approx_note not in self._approx_place_notes:
                self._approx_place_notes.append(approx_note)

        source = "|".join(dict.fromkeys(sources)) or "unknown"
        if not travel_alias:
            travel_alias = self._nearest_graph_id(float(lng), float(lat), district=district)
        reason = str(
            merged.get("reason")
            or (
                "联网检索后补进这一版，细节以现场为准"
                if geo_quality == "exact"
                else "坐标待核验，先按区划/邻近锚点软纳入这一版"
            )
        )
        row = build_synthetic_poi(
            display,
            city,
            district=district,
            gcj_lng=float(lng),
            gcj_lat=float(lat),
            poi_type_en=category,
            category_name=str(merged.get("category_name") or ""),
            reason=reason,
            travel_alias=travel_alias,
            geo_quality=geo_quality,
        )
        row["supplement_source"] = source
        self._register_ephemeral(row)
        record_poi_supplement(
            {
                "city": city,
                "user_text": getattr(intent, "utterance", "") or "",
                "want_place": name,
                "name": display,
                "district": district,
                "category": row.get("poi_type_en"),
                "category_name": row.get("category_name"),
                "gcj_lng": searched_lng,
                "gcj_lat": searched_lat,
                "poi_id": row["poi_id"],
                "source": source,
                "geo_quality": geo_quality,
                "reason": row.get("supplement_reason") or "",
                "evidence": evidence[:5],
            },
            data_dir=self.supplement_store_dir,
        )
        return row

    def _soft_geo_for_supplement(
        self,
        *,
        name: str,
        city: str,
        district: str,
        category: str,
        intent: Intent,
    ) -> dict:
        """Quality ladder (b)/(c): district centroid / catalog neighbor / prefer_near / city pin."""
        # (b) district known → centroid or nearest catalog POI in that district + category.
        if district:
            catalog = self._district_catalog_anchor(city, district, category)
            if catalog is not None:
                return catalog
            centroid = district_centroid(city, district)
            if centroid is not None:
                lng, lat = centroid
                return {
                    "gcj_lng": lng,
                    "gcj_lat": lat,
                    "district": district,
                    "geo_quality": "district",
                    "travel_alias": self._nearest_graph_id(lng, lat, district=district),
                }

        # (c) name only → prefer_near / sibling want_places / city-center prior.
        near = (getattr(intent, "prefer_near", None) or "").strip()
        if near:
            near_id = self._resolve_named_place(near, kind="area")
            anchored = self._coords_for_poi_id(near_id)
            if anchored is not None:
                lng, lat, near_district = anchored
                return {
                    "gcj_lng": lng,
                    "gcj_lat": lat,
                    "district": near_district or district,
                    "geo_quality": "anchor",
                    "travel_alias": near_id,
                }

        for sibling in getattr(intent, "want_places", None) or []:
            if not sibling or sibling == name:
                continue
            sib_id = self._resolve_named_place(sibling, kind="place")
            if not sib_id:
                continue
            anchored = self._coords_for_poi_id(sib_id)
            if anchored is None:
                continue
            lng, lat, sib_district = anchored
            return {
                "gcj_lng": lng,
                "gcj_lat": lat,
                "district": sib_district or district,
                "geo_quality": "anchor",
                "travel_alias": sib_id,
            }

        pin = city_pin(city)
        lng, lat = float(pin["gcj_lng"]), float(pin["gcj_lat"])
        return {
            "gcj_lng": lng,
            "gcj_lat": lat,
            "district": district or str(pin.get("district") or ""),
            "geo_quality": "city_prior",
            "travel_alias": self._nearest_graph_id(lng, lat, district=str(pin.get("district") or "")),
        }

    def _district_catalog_anchor(
        self, city: str, district: str, category: str
    ) -> dict | None:
        graph_ids = set(map(str, self.distances.keys()))
        best = None
        best_score = -1e9
        for row in self.pois.to_dict("records"):
            if str(row.get("district") or "") != district:
                continue
            if self._city_key(row) != city:
                continue
            poi_id = str(row.get("poi_id") or "")
            if not poi_id or poi_id.startswith("ext:"):
                continue
            if pd.isna(row.get("gcj_lng")) or pd.isna(row.get("gcj_lat")):
                continue
            score = 0.0
            if str(row.get("poi_type_en") or "") == category:
                score += 40.0
            if str(row.get("is_main_site", "")).lower() in {"true", "1"}:
                score += 20.0
            if poi_id in graph_ids:
                score += 10.0
            try:
                score += min(10.0, float(row.get("popularity", 0) or 0) * 10.0)
            except (TypeError, ValueError):
                pass
            if score > best_score:
                best_score = score
                best = row
        if best is None:
            return None
        return {
            "gcj_lng": float(best["gcj_lng"]),
            "gcj_lat": float(best["gcj_lat"]),
            "district": district,
            "geo_quality": "district",
            "travel_alias": str(best["poi_id"]) if str(best["poi_id"]) in graph_ids else None,
        }

    def _coords_for_poi_id(self, poi_id: str | None) -> tuple[float, float, str] | None:
        if not poi_id:
            return None
        if str(poi_id) in self._coordinates:
            lng, lat = self._coordinates[str(poi_id)]
            row = self._row_by_id(str(poi_id)) or {}
            return float(lng), float(lat), str(row.get("district") or "")
        row = self._row_by_id(str(poi_id))
        if row is None or pd.isna(row.get("gcj_lng")) or pd.isna(row.get("gcj_lat")):
            return None
        return float(row["gcj_lng"]), float(row["gcj_lat"]), str(row.get("district") or "")

    def _nearest_graph_id(
        self, lng: float, lat: float, *, district: str = ""
    ) -> str | None:
        graph_ids = set(map(str, self.distances.keys()))
        best_id = None
        best = 1e9
        for row in self.pois.to_dict("records"):
            poi_id = str(row.get("poi_id") or "")
            if poi_id not in graph_ids or poi_id.startswith("ext:"):
                continue
            if pd.isna(row.get("gcj_lng")) or pd.isna(row.get("gcj_lat")):
                continue
            same = not district or str(row.get("district") or "") == district
            d_lng = float(row["gcj_lng"]) - lng
            d_lat = float(row["gcj_lat"]) - lat
            dist = d_lng * d_lng + d_lat * d_lat
            if same:
                dist -= 0.01
            if dist < best:
                best = dist
                best_id = poi_id
        return best_id

    def _register_ephemeral(self, row: dict) -> None:
        poi_id = str(row["poi_id"])
        self._ephemeral[poi_id] = dict(row)
        if row.get("gcj_lng") is not None and row.get("gcj_lat") is not None:
            self._coordinates[poi_id] = (float(row["gcj_lng"]), float(row["gcj_lat"]))
        uncertainty = row.get("_geo_uncertainty")
        if uncertainty is None:
            uncertainty = geo_uncertainty_factor(str(row.get("geo_quality") or "exact"))
        self._geo_uncertainty[poi_id] = float(uncertainty)
        alias = str(row.get("_travel_alias") or "")
        if alias:
            self._travel_aliases[poi_id] = alias
            alias_graph_node(self.distances, poi_id, alias)
            self._travel_cache.clear()

    def _resolve_named_place(
        self,
        name: str | None,
        candidates: list[dict] | None = None,
        *,
        kind: str = "area",
    ) -> str | None:
        label = (name or "").strip()
        if not label or label in {"上海", "北京", "市区", "这里"}:
            return None
        seen: set[str] = set()
        pool: list[dict] = []
        extra_rows = list(self._ephemeral.values())
        for row in list(candidates or []) + extra_rows + self.pois.to_dict("records"):
            poi_id = str(row.get("poi_id", ""))
            if not poi_id or poi_id in seen:
                continue
            seen.add(poi_id)
            pool.append(row)
        best: dict | None = None
        best_score = 0.0
        for row in pool:
            score = self._named_place_score(label, row, kind=kind)
            if score > best_score:
                best = row
                best_score = score
        return str(best["poi_id"]) if best is not None else None

    def _resolve_start(
        self, intent: Intent, candidates: list[dict]
    ) -> tuple[str | None, bool]:
        name = (intent.start_name or intent.named_poi or "").strip()
        if not name:
            return None, False
        matched = self._resolve_named_place(name, candidates)
        if matched is None:
            return None, False
        return matched, not intent.start_as_depot

    def _theme_seed_id(self, ranked: list[dict], intent: Intent) -> str | None:
        for row in ranked:
            if is_theme_signature(row, intent):
                return str(row["poi_id"])
        return str(ranked[0]["poi_id"]) if ranked else None

    def _hop_reachable_count(self, seed_id: str, pool_ids: list[str]) -> int:
        """How many pool POIs are reachable from seed under MAX_HOP_MINUTES edges."""
        if not pool_ids:
            return 0
        remaining = set(pool_ids)
        remaining.add(seed_id)
        seen = {seed_id}
        frontier = [seed_id]
        while frontier:
            current = frontier.pop()
            for other in remaining - seen:
                if self._travel(current, other) <= MAX_HOP_MINUTES:
                    seen.add(other)
                    frontier.append(other)
        return len(seen)

    def _density_aware_seed_id(self, ranked: list[dict], *, max_stops: int) -> str | None:
        """Prefer a well-connected seed among top-scored POIs to reduce too_sparse plans."""
        if not ranked:
            return None
        top = ranked[:12]
        ids = [str(row["poi_id"]) for row in top]
        top_id = ids[0]
        top_reach = self._hop_reachable_count(top_id, ids)
        need = min(max(3, max_stops), 5)
        if top_reach >= need:
            return top_id

        best_id = top_id
        best_key = (top_reach, float(top[0].get("_score", 0.0)))
        for row in top[1:]:
            poi_id = str(row["poi_id"])
            reach = self._hop_reachable_count(poi_id, ids)
            score = float(row.get("_score", 0.0))
            key = (reach, score)
            if key > best_key:
                best_key = key
                best_id = poi_id
        if best_key[0] > top_reach:
            return best_id
        return top_id

    def _coherent_pool(
        self,
        *,
        seed_id: str | None,
        ranked: list[dict],
        by_id: dict[str, dict],
    ) -> list[str]:
        if not ranked:
            return []
        if seed_id is None or seed_id not in by_id:
            seed_id = str(ranked[0]["poi_id"])
        kept: list[str] = []
        for row in ranked:
            poi_id = str(row["poi_id"])
            if poi_id == seed_id:
                kept.append(poi_id)
                continue
            travel = self._travel(seed_id, poi_id)
            distance = self._haversine_km(seed_id, poi_id)
            if travel <= CLUSTER_TRAVEL_MAX or (
                distance is not None and distance <= CLUSTER_DISTANCE_KM
            ):
                kept.append(poi_id)
        if len(kept) < 4:
            kept = [str(row["poi_id"]) for row in ranked[:BEAM_CANDIDATE_LIMIT]]
        return kept

    def _haversine_km(self, origin: str, destination: str) -> float | None:
        origin = self._travel_aliases.get(str(origin), str(origin))
        destination = self._travel_aliases.get(str(destination), str(destination))
        if origin not in self._coordinates or destination not in self._coordinates:
            return None
        lng1, lat1 = self._coordinates[origin]
        lng2, lat2 = self._coordinates[destination]
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)
        value = (
            math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    def _window_wait(self, poi: dict, arrival: float) -> tuple[float, bool]:
        windows = poi.get("opening_windows")
        verified = bool(poi.get("opening_hours_verified"))
        if not windows:
            parsed, verified, _flags = parse_opening_hours(poi.get("opentime"))
            windows = parsed
        if not windows or not verified:
            return 0.0, True
        best_wait: float | None = None
        for start_text, end_text in windows:
            start = self._clock_from_text(start_text)
            end = self._clock_from_text(end_text)
            if start is None or end is None:
                continue
            if end <= start:
                end += 24 * 60
            if start <= arrival <= end:
                return 0.0, True
            if arrival < start:
                wait = start - arrival
                if best_wait is None or wait < best_wait:
                    best_wait = wait
        if best_wait is not None and best_wait <= MAX_WINDOW_WAIT:
            return best_wait, True
        return 0.0, False

    def _windows_feasible(
        self,
        order: list[str],
        by_id: dict[str, dict],
        intent: Intent,
        start_clock: float,
        depot_id: str | None,
    ) -> bool:
        clock = start_clock
        last = depot_id
        for poi_id in order:
            travel = 0.0 if last is None else self._travel(last, poi_id)
            wait, feasible = self._window_wait(by_id[poi_id], clock + travel)
            if not feasible:
                return False
            clock = clock + travel + wait + self._dwell_for(by_id[poi_id], intent)
            last = poi_id
        return True

    @staticmethod
    def _clock_from_text(value: str) -> float | None:
        match = re.search(r"(\d{1,2}):(\d{2})", str(value))
        if not match:
            return None
        return int(match.group(1)) * 60 + int(match.group(2))

    @staticmethod
    def _format_clock(value: float) -> str:
        minutes = int(round(float(value))) % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    @staticmethod
    def _collect_realtime_notices(selected: list[dict]) -> list[dict]:
        notices: list[dict] = []
        seen: set[tuple[str, str | None, str]] = set()
        for poi in selected:
            for notice in poi.get("_realtime_notices", []):
                key = (
                    str(notice.get("category", "")),
                    notice.get("poi_id"),
                    str(notice.get("message", "")),
                )
                if key not in seen:
                    seen.add(key)
                    notices.append(dict(notice))
        return notices

    @staticmethod
    def _reason(poi: dict, intent: Intent) -> str:
        stable_summary = str(poi.get("_stable_summary", "")).strip()
        if stable_summary:
            return stable_summary
        if intent.mode == "deep":
            return "更符合你想深入了解当地文化和故事的状态"
        if intent.mode == "photo":
            return "景观表现突出，适合作为今天的拍摄重点"
        if intent.mode == "relaxed":
            return "节奏可以放慢，也方便根据现场状态随时调整"
        if intent.mode == "full":
            return "代表性和游览效率较高，适合充实地走完今天"
        return "在代表性、体验感和路线时间之间较为平衡"

    @staticmethod
    def _reminder(planned: float, available: float) -> tuple[str, str]:
        ratio = planned / max(available, 1.0)
        remaining = round(available - planned)
        if ratio <= 0.7:
            return "spacious", f"今天还留有约 {remaining} 分钟，不必急着把空白填满。"
        if ratio <= 0.9:
            return "comfortable", "这个节奏比较从容，路上想多停一会也没关系。"
        if ratio <= 1.0:
            return "tight", "时间会稍微紧一点，到现场看状态调整就好。"
        if ratio <= 1.2:
            return "over", "今天可能有点满，可以先保留最想去的地方。"
        return "overflow", "先选两三个最想去的，剩下的留作下次再来的理由。"
