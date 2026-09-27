"""Chance-constrained time budgeting for route planning.

Model
-----
Let ``T`` be the traveller's usable time and ``D`` the realised duration of a
route. Planning must respect the chance constraint

    P(D <= T) >= 1 - epsilon.

``T ~ (mu, sigma^2)`` is the elicited time ("about four hours"), and ``D`` may
itself be uncertain with mean ``m_D`` and standard deviation ``sigma_D``.
Writing ``Z = D - T`` the constraint is ``P(Z <= 0) >= 1 - epsilon``, and every
model below yields a deterministic bound of the same shape

    m_D <= mu - kappa(epsilon) * s,      s = sqrt(sigma^2 + sigma_D^2)

so the planner only ever compares accumulated minutes against one number.

Supported ``kappa`` choices, from least to most conservative:

``gaussian``
    Exact reformulation when ``Z`` is normal: ``kappa = Phi^-1(1 - epsilon)``.
``cvar``
    Conditional Value-at-Risk safe approximation. ``CVaR_{1-eps}(Z) <= 0``
    implies the chance constraint because CVaR >= VaR (Rockafellar & Uryasev
    2000; Nemirovski & Shapiro, *Convex Approximations of Chance Constrained
    Programs*, SIAM J. Optim. 17(4), 2006, eq. 2.11). For normal ``Z`` this
    gives ``kappa = phi(Phi^-1(1 - epsilon)) / epsilon``.
``moment``
    Distribution-free bound using only mean and variance (Cantelli / Scarf;
    Calafiore & El Ghaoui, *Distributionally robust chance-constrained linear
    programs*, Thm. 3.1): ``kappa = sqrt((1 - epsilon) / epsilon)``.
``wasserstein``
    Type-1 Wasserstein distributionally robust CVaR. For a radius ``theta``
    ball around the nominal distribution the worst-case CVaR of a 1-Lipschitz
    loss increases by ``theta / epsilon``, so the budget shrinks by the same
    amount on top of the ``cvar`` bound.

The previous implementation subtracted ``theta * sigma``, which has no such
guarantee, and silently clamped infeasible budgets to 30 minutes so that the
returned number no longer satisfied the chance constraint. Both are fixed here:
``theta / epsilon`` is used, and clamping is reported through
:class:`TimeBudget.feasible`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, floor, pi, sqrt
from statistics import NormalDist

RISK_MODELS = ("gaussian", "cvar", "moment", "wasserstein")
DEFAULT_RISK_MODEL = "gaussian"
MIN_BUDGET_MINUTES = 30.0

_NORMAL = NormalDist()


def _standard_normal_pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * pi)


def risk_multiplier(
    epsilon: float,
    model: str = DEFAULT_RISK_MODEL,
) -> float:
    """Return ``kappa(epsilon)`` for the requested reformulation."""
    _validate_epsilon(epsilon)
    if model not in RISK_MODELS:
        raise ValueError(f"unknown risk model: {model!r}; expected one of {RISK_MODELS}")

    quantile = _NORMAL.inv_cdf(1.0 - epsilon)
    if model == "gaussian":
        return quantile
    if model in ("cvar", "wasserstein"):
        return _standard_normal_pdf(quantile) / epsilon
    return sqrt((1.0 - epsilon) / epsilon)


@dataclass(frozen=True, slots=True)
class TimeBudget:
    """Deterministic minute budget plus the diagnostics needed to audit it."""

    minutes: float
    raw_minutes: float
    feasible: bool
    model: str
    epsilon: float
    kappa: float
    dispersion_minutes: float

    @property
    def was_clamped(self) -> bool:
        return self.minutes > self.raw_minutes + 1e-9


def time_budget(
    mean_minutes: float,
    sigma_minutes: float,
    epsilon: float = 0.1,
    *,
    model: str = DEFAULT_RISK_MODEL,
    route_sigma_minutes: float = 0.0,
    wasserstein_radius: float = 0.0,
    min_budget_minutes: float = MIN_BUDGET_MINUTES,
) -> TimeBudget:
    """Largest planned duration whose overrun risk stays at or below ``epsilon``."""
    if mean_minutes <= 0:
        raise ValueError("mean_minutes must be positive")
    if sigma_minutes < 0:
        raise ValueError("sigma_minutes cannot be negative")
    if route_sigma_minutes < 0:
        raise ValueError("route_sigma_minutes cannot be negative")
    if wasserstein_radius < 0:
        raise ValueError("wasserstein_radius cannot be negative")
    _validate_epsilon(epsilon)

    kappa = risk_multiplier(epsilon, model)
    dispersion = sqrt(sigma_minutes**2 + route_sigma_minutes**2)
    raw = mean_minutes - kappa * dispersion
    if model == "wasserstein":
        raw -= wasserstein_radius / epsilon
    elif wasserstein_radius:
        raise ValueError(
            "wasserstein_radius is only meaningful for model='wasserstein'"
        )

    # Round the budget down, never up: rounding up by even 0.1 minute would let
    # a route sit just above the quantile and break the chance constraint.
    reported = floor(raw * 10.0) / 10.0
    return TimeBudget(
        minutes=max(min_budget_minutes, reported),
        raw_minutes=reported,
        feasible=reported >= min_budget_minutes,
        model=model,
        epsilon=epsilon,
        kappa=kappa,
        dispersion_minutes=dispersion,
    )


def safe_time_budget(
    mean_minutes: float,
    sigma_minutes: float,
    epsilon: float = 0.1,
    *,
    model: str = DEFAULT_RISK_MODEL,
    route_sigma_minutes: float = 0.0,
    wasserstein_radius: float = 0.0,
) -> float:
    """Minutes-only view of :func:`time_budget`, kept for existing callers."""
    return time_budget(
        mean_minutes,
        sigma_minutes,
        epsilon,
        model="wasserstein" if wasserstein_radius else model,
        route_sigma_minutes=route_sigma_minutes,
        wasserstein_radius=wasserstein_radius,
    ).minutes


def completion_probability(
    route_minutes: float,
    mean_minutes: float,
    sigma_minutes: float,
    *,
    route_sigma_minutes: float = 0.0,
) -> float:
    """``P(D <= T)`` under the normal model used to derive the budget."""
    dispersion = sqrt(max(sigma_minutes, 0.0) ** 2 + max(route_sigma_minutes, 0.0) ** 2)
    if dispersion <= 0:
        return 1.0 if route_minutes <= mean_minutes else 0.0
    z = (mean_minutes - route_minutes) / dispersion
    return round(0.5 * (1.0 + erf(z / sqrt(2.0))), 4)


def chance_constraint_satisfied(
    route_minutes: float,
    mean_minutes: float,
    sigma_minutes: float,
    epsilon: float = 0.1,
    *,
    route_sigma_minutes: float = 0.0,
) -> bool:
    """Whether a concrete route meets ``P(D <= T) >= 1 - epsilon``."""
    _validate_epsilon(epsilon)
    probability = completion_probability(
        route_minutes,
        mean_minutes,
        sigma_minutes,
        route_sigma_minutes=route_sigma_minutes,
    )
    return probability + 1e-9 >= 1.0 - epsilon


def _validate_epsilon(epsilon: float) -> None:
    if not 0 < epsilon < 0.5:
        raise ValueError("epsilon must be between 0 and 0.5")
