"""Kalman tracking of continuous travel preferences.

State
-----
``x`` holds the six POI attribute affinities used by the scorer plus a pace
dimension, all on a ``[0, 1]`` scale. Dynamics are a random walk (``F = I``),
which is the standard model for slowly drifting preferences, so the filter is

    predict:  P <- P + Q
    update:   S = H P H' + R,  K = P H' S^-1,
              x <- x + K (z - H x),
              P <- (I - K H) P (I - K H)' + K R K'.

Three properties matter for correctness here:

*Partial observations.* Every measurement carries the dimensions it actually
observed. ``H`` selects those rows. Earlier versions padded unobserved
dimensions with filler values, so reporting a pace dragged the attribute
estimates back toward the "balanced" profile and accepting a POI dragged pace
toward 0.5.

*Joseph form.* ``P`` is updated with the stabilised form above rather than
``P - K S K'``, which avoids differencing nearly equal covariance matrices and
keeps ``P`` symmetric positive definite (Joseph 1968; standard in filtering
practice).

*Innovation gating.* The normalised innovation squared ``nu' S^-1 nu`` follows a
chi-square law with ``m`` degrees of freedom, so outliers are rejected against a
chi-square quantile of the observed dimension rather than a fixed constant. This
also keeps the filter from becoming confidently wrong: eigenvalues of ``P`` are
floored, and gated measurements inflate ``P`` instead of shrinking it.

Discrete commands (delete a stop, replan for rain) must NOT pass through this
filter; they belong to PlanOps / replan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

import numpy as np

ATTR_COLUMNS = [
    "cultural_score",
    "nature_score",
    "commercial_score",
    "scenic_score",
    "indoor_score",
    "edu_score",
]

MODE_WEIGHTS = {
    "relaxed": np.array([0.35, 0.85, 0.10, 0.80, 0.15, 0.20]),
    "full": np.array([0.55, 0.45, 0.10, 0.75, 0.30, 0.35]),
    "deep": np.array([1.00, 0.25, 0.05, 0.70, 0.40, 0.90]),
    "photo": np.array([0.35, 0.80, 0.10, 1.00, 0.05, 0.15]),
    "balanced": np.array([0.60, 0.60, 0.15, 0.80, 0.25, 0.40]),
}

PREFERENCE_DIMS = [*ATTR_COLUMNS, "pace"]
PACE_INDEX = len(ATTR_COLUMNS)
ATTR_DIMS = tuple(range(len(ATTR_COLUMNS)))
PACE_DIMS = (PACE_INDEX,)

MODE_TO_OBSERVATION = {
    "relaxed": np.array([0.35, 0.85, 0.10, 0.80, 0.15, 0.20, 0.2]),
    "full": np.array([0.55, 0.45, 0.10, 0.75, 0.30, 0.35, 0.7]),
    "deep": np.array([1.00, 0.25, 0.05, 0.70, 0.40, 0.90, 0.4]),
    "photo": np.array([0.35, 0.80, 0.10, 1.00, 0.05, 0.15, 0.5]),
    "balanced": np.array([0.60, 0.60, 0.15, 0.80, 0.25, 0.40, 0.5]),
}

PACE_TO_VALUE = {"slow": 0.2, "normal": 0.5, "fast": 0.8}

_NORMAL = NormalDist()


def chi2_quantile(probability: float, dof: int) -> float:
    """Wilson-Hilferty approximation of the chi-square quantile.

    Accurate to well under 1% for the dimensions used here, which avoids a
    SciPy dependency while keeping the gate tied to the observed dimension.
    """
    if dof < 1:
        raise ValueError("dof must be at least 1")
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be in (0, 1)")
    z = _NORMAL.inv_cdf(probability)
    term = 1.0 - 2.0 / (9.0 * dof) + z * np.sqrt(2.0 / (9.0 * dof))
    return float(max(dof * term**3, 1e-9))


@dataclass(slots=True)
class PreferenceObservation:
    """A soft measurement of a subset of the preference state."""

    kind: str
    values: np.ndarray
    dims: tuple[int, ...] | None = None
    confidence: float = 1.0
    source: str = ""

    def __post_init__(self) -> None:
        vector = np.asarray(self.values, dtype=float).reshape(-1)
        if self.dims is None:
            if vector.shape[0] != len(PREFERENCE_DIMS):
                raise ValueError(
                    f"full observation must have {len(PREFERENCE_DIMS)} dims, "
                    f"got {vector.shape[0]}"
                )
            self.dims = tuple(range(len(PREFERENCE_DIMS)))
        else:
            self.dims = tuple(int(index) for index in self.dims)
            if len(set(self.dims)) != len(self.dims):
                raise ValueError("observation dims must be unique")
            if any(index < 0 or index >= len(PREFERENCE_DIMS) for index in self.dims):
                raise ValueError("observation dims out of range")
            if vector.shape[0] != len(self.dims):
                raise ValueError(
                    f"observation has {vector.shape[0]} values for {len(self.dims)} dims"
                )
        self.values = np.clip(vector, 0.0, 1.0)
        self.confidence = float(np.clip(self.confidence, 0.05, 1.0))

    @property
    def dimension(self) -> int:
        return len(self.values)


def observation_from_mode(mode: str, *, confidence: float = 0.55) -> PreferenceObservation:
    """Attribute-only measurement implied by a travel mode."""
    profile = MODE_TO_OBSERVATION.get(mode, MODE_TO_OBSERVATION["balanced"])
    return PreferenceObservation(
        kind="intent_mode",
        values=profile[: len(ATTR_COLUMNS)].copy(),
        dims=ATTR_DIMS,
        confidence=confidence,
        source=mode,
    )


def observation_from_pace(pace: str, *, confidence: float = 0.45) -> PreferenceObservation:
    """Pace-only measurement; leaves attribute affinities untouched."""
    return PreferenceObservation(
        kind="intent_pace",
        values=np.array([PACE_TO_VALUE.get(pace, 0.5)], dtype=float),
        dims=PACE_DIMS,
        confidence=confidence,
        source=pace,
    )


def observation_from_poi_attributes(
    attributes: Sequence[float] | Mapping[str, float],
    *,
    accepted: bool = True,
    confidence: float = 0.65,
    salience: float = 0.15,
    source: str = "",
) -> PreferenceObservation:
    """Measurement from accepting or rejecting a POI.

    Acceptance is evidence that the user's affinities resemble the POI profile.
    Rejection is weaker, one-sided evidence: it only informs the dimensions on
    which the POI is distinctive (``|a_i - 0.5| >= salience``), and there it
    points to the opposite side. Dimensions the POI says nothing about are left
    unobserved rather than pushed toward a filler value.
    """
    if isinstance(attributes, Mapping):
        vector = np.array(
            [float(attributes.get(name, 0.5)) for name in ATTR_COLUMNS], dtype=float
        )
    else:
        raw = [float(value) for value in attributes]
        vector = np.asarray(raw[: len(ATTR_COLUMNS)], dtype=float)
        if vector.shape[0] != len(ATTR_COLUMNS):
            raise ValueError(f"expected {len(ATTR_COLUMNS)} POI attributes")

    if accepted:
        return PreferenceObservation(
            kind="accept_poi",
            values=vector,
            dims=ATTR_DIMS,
            confidence=confidence,
            source=source,
        )

    distinctive = [
        index for index in ATTR_DIMS if abs(float(vector[index]) - 0.5) >= salience
    ]
    if not distinctive:
        distinctive = list(ATTR_DIMS)
    return PreferenceObservation(
        kind="reject_poi",
        values=np.array([1.0 - float(vector[index]) for index in distinctive], dtype=float),
        dims=tuple(distinctive),
        confidence=confidence * 0.6,
        source=source,
    )


def observation_from_dwell_ratio(
    actual_minutes: float,
    planned_minutes: float,
    *,
    confidence: float = 0.4,
) -> PreferenceObservation:
    """Pace-only measurement: staying longer than planned means a slower pace."""
    planned = max(float(planned_minutes), 1.0)
    ratio = float(actual_minutes) / planned
    pace = float(np.clip(0.5 - (ratio - 1.0) * 0.35, 0.0, 1.0))
    return PreferenceObservation(
        kind="dwell_ratio",
        values=np.array([pace], dtype=float),
        dims=PACE_DIMS,
        confidence=confidence,
        source=f"ratio={ratio:.2f}",
    )


@dataclass
class PreferenceTracker:
    """Random-walk Kalman filter over the preference state."""

    state: np.ndarray = field(
        default_factory=lambda: MODE_TO_OBSERVATION["balanced"].copy()
    )
    covariance: np.ndarray = field(
        default_factory=lambda: np.eye(len(PREFERENCE_DIMS)) * 0.25
    )
    process_noise: float = 0.02
    measurement_noise: float = 0.08
    min_variance: float = 0.01
    max_variance: float = 2.0
    gate_probability: float = 0.999
    baseline_variance: float = 0.25

    def __post_init__(self) -> None:
        size = len(PREFERENCE_DIMS)
        self.state = np.asarray(self.state, dtype=float).reshape(size).copy()
        covariance = np.asarray(self.covariance, dtype=float)
        if covariance.shape != (size, size):
            covariance = np.eye(size) * 0.25
        self.covariance = covariance.copy()
        self._condition_covariance()

    # ------------------------------------------------------------------ steps

    def predict(self) -> None:
        """Advance one time step: ``P <- F P F' + Q`` with ``F = I``."""
        size = len(PREFERENCE_DIMS)
        self.covariance = self.covariance + np.eye(size) * max(self.process_noise, 0.0)
        self._condition_covariance()

    def update(self, observation: PreferenceObservation, *, advance: bool = True) -> bool:
        """Fuse one measurement. Returns ``False`` when it is gated out."""
        if advance:
            self.predict()

        dims = list(observation.dims or ())
        z = observation.values
        m = len(dims)
        prior = self.covariance
        innovation = z - self.state[dims]
        noise = self.measurement_noise / max(observation.confidence, 0.05)
        s = prior[np.ix_(dims, dims)] + np.eye(m) * noise
        s = 0.5 * (s + s.T)

        try:
            solved = np.linalg.solve(s, innovation)
            cross = prior[:, dims]
            gain = np.linalg.solve(s, cross.T).T
        except np.linalg.LinAlgError:
            return False

        nis = float(innovation @ solved)
        if nis > chi2_quantile(self.gate_probability, m):
            # Reject the outlier, and widen P so the filter does not stay
            # confidently wrong about a state it may have lost track of.
            self.covariance = prior + np.eye(len(PREFERENCE_DIMS)) * (
                max(self.process_noise, 0.0) * 0.5
            )
            self._condition_covariance()
            return False

        self.state = self.state + gain @ innovation

        size = len(PREFERENCE_DIMS)
        a = np.eye(size)
        a[:, dims] -= gain
        self.covariance = a @ prior @ a.T + gain @ (np.eye(m) * noise) @ gain.T
        self._condition_covariance()
        return True

    def step(self, observations: Iterable[PreferenceObservation]) -> int:
        """One time step carrying several measurements; predicts exactly once."""
        self.predict()
        accepted = 0
        for observation in observations:
            if self.update(observation, advance=False):
                accepted += 1
        return accepted

    def update_many(self, observations: Iterable[PreferenceObservation]) -> int:
        return self.step(observations)

    def observe_intent(self, mode: str, pace: str = "normal") -> int:
        return self.step(
            [
                observation_from_mode(mode),
                observation_from_pace(pace, confidence=0.35),
            ]
        )

    # ----------------------------------------------------------------- output

    def score_weights(
        self,
        mode: str | None = None,
        *,
        blend: float | None = None,
    ) -> np.ndarray:
        """Attribute weights for the scorer.

        By default the filter mean is fused with the mode baseline by inverse
        variance, so an uncertain filter defers to the baseline and a confident
        one overrides it. Passing ``blend`` forces a fixed mix instead.

        Output always sums to the reference mass so that the scorer's travel and
        diversity penalties keep the same relative weight across modes.
        """
        baseline = MODE_WEIGHTS.get(mode or "balanced", MODE_WEIGHTS["balanced"])
        reference_mass = float(MODE_WEIGHTS["balanced"].sum())
        estimated = np.clip(self.state[: len(ATTR_COLUMNS)], 0.0, 1.0)
        if blend is None:
            variances = np.clip(
                np.diag(self.covariance)[: len(ATTR_COLUMNS)], self.min_variance, None
            )
            tau2 = max(self.baseline_variance, 1e-9)
            weights = (tau2 * estimated + variances * baseline) / (tau2 + variances)
        else:
            mix = float(np.clip(blend, 0.0, 1.0))
            weights = mix * estimated + (1.0 - mix) * baseline
        weights = np.clip(weights, 0.05, None)
        return weights / max(float(weights.sum()), 1e-9) * reference_mass

    def confidence(self) -> float:
        """Average attribute confidence in ``[0, 1]``; 1 means fully settled."""
        variances = np.clip(
            np.diag(self.covariance)[: len(ATTR_COLUMNS)], self.min_variance, None
        )
        tau2 = max(self.baseline_variance, 1e-9)
        return round(float(np.mean(tau2 / (tau2 + variances))), 4)

    def pace_hint(self) -> str:
        value = float(np.clip(self.state[PACE_INDEX], 0.0, 1.0))
        if value < 0.35:
            return "slow"
        if value > 0.65:
            return "fast"
        return "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dims": list(PREFERENCE_DIMS),
            "state": {
                name: round(float(self.state[i]), 6)
                for i, name in enumerate(PREFERENCE_DIMS)
            },
            "variance": {
                name: round(float(self.covariance[i, i]), 6)
                for i, name in enumerate(PREFERENCE_DIMS)
            },
            "covariance": [
                [round(float(value), 6) for value in row] for row in self.covariance
            ],
            "confidence": self.confidence(),
            "pace_hint": self.pace_hint(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> PreferenceTracker:
        tracker = cls()
        if not payload:
            return tracker
        state = payload.get("state")
        if isinstance(state, dict):
            tracker.state = np.array(
                [
                    float(state.get(name, tracker.state[i]))
                    for i, name in enumerate(PREFERENCE_DIMS)
                ],
                dtype=float,
            )
        matrix = payload.get("covariance")
        size = len(PREFERENCE_DIMS)
        if isinstance(matrix, list) and len(matrix) == size:
            tracker.covariance = np.array(matrix, dtype=float).reshape(size, size)
        else:
            variance = payload.get("variance")
            if isinstance(variance, dict):
                tracker.covariance = np.diag(
                    [
                        float(variance.get(name, tracker.covariance[i, i]))
                        for i, name in enumerate(PREFERENCE_DIMS)
                    ]
                )
        tracker._condition_covariance()
        return tracker

    # ---------------------------------------------------------------- internal

    def _condition_covariance(self) -> None:
        """Keep ``P`` symmetric with eigenvalues inside ``[min_var, max_var]``."""
        matrix = 0.5 * (self.covariance + self.covariance.T)
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)
        eigenvalues = np.clip(eigenvalues, self.min_variance, self.max_variance)
        self.covariance = (eigenvectors * eigenvalues) @ eigenvectors.T
        self.covariance = 0.5 * (self.covariance + self.covariance.T)
