import random
import unittest
from statistics import NormalDist

from tripsense.core.constraints import (
    RISK_MODELS,
    chance_constraint_satisfied,
    completion_probability,
    risk_multiplier,
    safe_time_budget,
    time_budget,
)


class ConstraintTests(unittest.TestCase):
    def test_safe_budget_uses_lower_quantile(self):
        budget = safe_time_budget(240, 30, 0.1)
        # 240 - Phi^-1(0.9) * 30 = 201.55, floored so the constraint still holds.
        self.assertAlmostEqual(budget, 201.5, places=1)
        self.assertAlmostEqual(completion_probability(budget, 240, 30), 0.9, places=3)
        self.assertGreaterEqual(completion_probability(budget, 240, 30), 0.9)

    def test_more_uncertainty_is_more_conservative(self):
        self.assertLess(safe_time_budget(240, 60, 0.1), safe_time_budget(240, 20, 0.1))

    def test_robust_radius_reduces_budget(self):
        base = safe_time_budget(240, 30, 0.1)
        robust = safe_time_budget(240, 30, 0.1, wasserstein_radius=0.2)
        self.assertLess(robust, base)


class RiskMultiplierTests(unittest.TestCase):
    def test_known_closed_forms_at_epsilon_one_tenth(self):
        # Phi^-1(0.9), phi(Phi^-1(0.9))/0.1, sqrt(0.9/0.1)
        self.assertAlmostEqual(risk_multiplier(0.1, "gaussian"), 1.281552, places=5)
        self.assertAlmostEqual(risk_multiplier(0.1, "cvar"), 1.754983, places=5)
        self.assertAlmostEqual(risk_multiplier(0.1, "moment"), 3.0, places=9)

    def test_conservativeness_ordering_holds_across_epsilons(self):
        for epsilon in (0.01, 0.05, 0.1, 0.2, 0.4):
            gaussian = risk_multiplier(epsilon, "gaussian")
            cvar = risk_multiplier(epsilon, "cvar")
            moment = risk_multiplier(epsilon, "moment")
            self.assertLess(gaussian, cvar, epsilon)
            self.assertLess(cvar, moment, epsilon)

    def test_unknown_model_and_bad_epsilon_are_rejected(self):
        with self.assertRaises(ValueError):
            risk_multiplier(0.1, "magic")
        for epsilon in (0.0, 0.5, 0.9, -0.1):
            with self.assertRaises(ValueError):
                risk_multiplier(epsilon, "gaussian")


class TimeBudgetTests(unittest.TestCase):
    def test_gaussian_budget_matches_monte_carlo_coverage(self):
        mean, sigma, epsilon = 240.0, 40.0, 0.1
        budget = time_budget(mean, sigma, epsilon).minutes
        rng = random.Random(20260924)
        normal = NormalDist(mean, sigma)
        trials = 40000
        covered = sum(1 for _ in range(trials) if normal.inv_cdf(rng.random()) >= budget)
        self.assertGreater(covered / trials, 1 - epsilon - 0.01)

    def test_more_conservative_models_shrink_the_budget(self):
        budgets = {
            model: time_budget(240, 40, 0.1, model=model, wasserstein_radius=0.0).minutes
            for model in ("gaussian", "cvar", "moment")
        }
        self.assertGreater(budgets["gaussian"], budgets["cvar"])
        self.assertGreater(budgets["cvar"], budgets["moment"])

    def test_wasserstein_radius_shifts_budget_by_theta_over_epsilon(self):
        nominal = time_budget(240, 40, 0.1, model="wasserstein").minutes
        robust = time_budget(240, 40, 0.1, model="wasserstein", wasserstein_radius=3.0)
        self.assertAlmostEqual(nominal - robust.minutes, 3.0 / 0.1, places=1)
        self.assertEqual(robust.model, "wasserstein")

    def test_radius_requires_the_wasserstein_model(self):
        with self.assertRaises(ValueError):
            time_budget(240, 40, 0.1, model="gaussian", wasserstein_radius=1.0)

    def test_route_uncertainty_is_pooled_into_the_dispersion(self):
        pooled = time_budget(240, 30, 0.1, route_sigma_minutes=40)
        self.assertAlmostEqual(pooled.dispersion_minutes, 50.0, places=6)
        self.assertLess(pooled.minutes, time_budget(240, 30, 0.1).minutes)

    def test_infeasible_budget_is_reported_not_silently_clamped(self):
        budget = time_budget(60, 60, 0.1)
        self.assertFalse(budget.feasible)
        self.assertTrue(budget.was_clamped)
        self.assertLess(budget.raw_minutes, budget.minutes)
        self.assertFalse(chance_constraint_satisfied(budget.minutes, 60, 60, 0.1))

    def test_feasible_budget_satisfies_the_chance_constraint(self):
        budget = time_budget(240, 30, 0.1)
        self.assertTrue(budget.feasible)
        self.assertFalse(budget.was_clamped)
        self.assertTrue(chance_constraint_satisfied(budget.minutes, 240, 30, 0.1))

    def test_every_model_is_reachable_through_the_public_api(self):
        for model in RISK_MODELS:
            radius = 1.0 if model == "wasserstein" else 0.0
            budget = time_budget(300, 30, 0.1, model=model, wasserstein_radius=radius)
            self.assertEqual(budget.model, model)
            self.assertGreater(budget.minutes, 0)


class CompletionProbabilityTests(unittest.TestCase):
    def test_zero_dispersion_is_a_step_function(self):
        self.assertEqual(completion_probability(100, 120, 0), 1.0)
        self.assertEqual(completion_probability(140, 120, 0), 0.0)

    def test_route_uncertainty_lowers_the_probability(self):
        base = completion_probability(200, 240, 30)
        pooled = completion_probability(200, 240, 30, route_sigma_minutes=40)
        self.assertLess(pooled, base)


if __name__ == "__main__":
    unittest.main()
