import unittest

import numpy as np

from tripsense.core.preference import (
    ATTR_COLUMNS,
    ATTR_DIMS,
    PACE_DIMS,
    PACE_INDEX,
    PREFERENCE_DIMS,
    PreferenceObservation,
    PreferenceTracker,
    chi2_quantile,
    observation_from_dwell_ratio,
    observation_from_mode,
    observation_from_pace,
    observation_from_poi_attributes,
)


class Chi2QuantileTests(unittest.TestCase):
    def test_matches_reference_values_within_one_percent(self):
        # Reference chi-square quantiles.
        reference = {
            (0.99, 1): 6.6349,
            (0.99, 6): 16.8119,
            (0.999, 6): 22.4577,
            (0.999, 7): 24.3219,
        }
        for (probability, dof), expected in reference.items():
            approximation = chi2_quantile(probability, dof)
            self.assertLess(
                abs(approximation - expected) / expected,
                0.01,
                f"p={probability} dof={dof}: {approximation} vs {expected}",
            )

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(ValueError):
            chi2_quantile(0.99, 0)
        with self.assertRaises(ValueError):
            chi2_quantile(1.0, 3)


class MaskedObservationTests(unittest.TestCase):
    def test_pace_observation_leaves_attributes_untouched(self):
        tracker = PreferenceTracker()
        for _ in range(4):
            tracker.update(observation_from_mode("deep", confidence=0.85))
        attributes_before = tracker.state[: len(ATTR_COLUMNS)].copy()

        tracker.update(observation_from_pace("slow", confidence=0.9))

        np.testing.assert_allclose(
            tracker.state[: len(ATTR_COLUMNS)], attributes_before, atol=1e-12
        )
        self.assertLess(float(tracker.state[PACE_INDEX]), 0.5)

    def test_poi_observation_leaves_pace_untouched(self):
        tracker = PreferenceTracker()
        tracker.update(observation_from_pace("fast", confidence=0.9))
        pace_before = float(tracker.state[PACE_INDEX])

        tracker.update(
            observation_from_poi_attributes([0.9, 0.1, 0.1, 0.2, 0.2, 0.8], confidence=0.9)
        )

        self.assertAlmostEqual(float(tracker.state[PACE_INDEX]), pace_before, places=12)

    def test_intent_observation_does_not_undo_its_own_mode_signal(self):
        with_pace = PreferenceTracker()
        with_pace.observe_intent("deep", "normal")
        mode_only = PreferenceTracker()
        mode_only.step([observation_from_mode("deep")])

        self.assertAlmostEqual(
            float(with_pace.state[0]), float(mode_only.state[0]), places=12
        )

    def test_rejection_only_informs_distinctive_dimensions(self):
        observation = observation_from_poi_attributes(
            [0.95, 0.5, 0.5, 0.5, 0.5, 0.5], accepted=False, confidence=0.9
        )
        self.assertEqual(observation.dims, (0,))
        self.assertAlmostEqual(float(observation.values[0]), 0.05, places=6)

    def test_accept_and_reject_poi_move_in_opposite_directions(self):
        profile = [0.95, 0.1, 0.1, 0.2, 0.2, 0.8]
        up = PreferenceTracker()
        down = PreferenceTracker()
        up.update(observation_from_poi_attributes(profile, accepted=True, confidence=0.9))
        down.update(observation_from_poi_attributes(profile, accepted=False, confidence=0.9))
        self.assertGreater(float(up.state[0]), float(down.state[0]))

    def test_observation_shape_validation(self):
        with self.assertRaises(ValueError):
            PreferenceObservation(kind="bad", values=np.zeros(3))
        with self.assertRaises(ValueError):
            PreferenceObservation(kind="bad", values=np.zeros(2), dims=ATTR_DIMS)
        with self.assertRaises(ValueError):
            PreferenceObservation(kind="bad", values=np.zeros(1), dims=(99,))
        with self.assertRaises(ValueError):
            PreferenceObservation(kind="bad", values=np.zeros(2), dims=(0, 0))


class FilterInvariantTests(unittest.TestCase):
    def test_covariance_stays_symmetric_and_positive_definite(self):
        tracker = PreferenceTracker()
        rng = np.random.default_rng(20260924)
        for _ in range(60):
            dims = tuple(sorted(rng.choice(len(PREFERENCE_DIMS), size=3, replace=False)))
            tracker.update(
                PreferenceObservation(
                    kind="synthetic",
                    values=rng.random(3),
                    dims=dims,
                    confidence=float(rng.uniform(0.2, 1.0)),
                )
            )
            matrix = tracker.covariance
            np.testing.assert_allclose(matrix, matrix.T, atol=1e-10)
            eigenvalues = np.linalg.eigvalsh(matrix)
            self.assertGreaterEqual(float(eigenvalues.min()), tracker.min_variance - 1e-9)
            self.assertLessEqual(float(eigenvalues.max()), tracker.max_variance + 1e-9)

    def test_off_diagonal_correlation_is_preserved(self):
        tracker = PreferenceTracker()
        tracker.covariance = np.full((len(PREFERENCE_DIMS), len(PREFERENCE_DIMS)), 0.05)
        tracker.covariance += np.eye(len(PREFERENCE_DIMS)) * 0.2
        tracker._condition_covariance()

        tracker.update(observation_from_mode("deep", confidence=0.9))

        off_diagonal = tracker.covariance - np.diag(np.diag(tracker.covariance))
        self.assertGreater(float(np.abs(off_diagonal).max()), 1e-6)

    def test_gate_rejects_outliers_and_widens_covariance(self):
        tracker = PreferenceTracker(measurement_noise=0.01)
        for _ in range(8):
            tracker.update(observation_from_mode("deep", confidence=0.9))
        trace_before = float(np.trace(tracker.covariance))

        outlier = PreferenceObservation(
            kind="outlier",
            values=np.zeros(len(ATTR_COLUMNS)),
            dims=ATTR_DIMS,
            confidence=1.0,
        )
        accepted = tracker.update(outlier)

        self.assertFalse(accepted)
        self.assertGreater(float(np.trace(tracker.covariance)), trace_before)

    def test_gate_is_calibrated_and_not_trigger_happy(self):
        tracker = PreferenceTracker(measurement_noise=0.01)
        for _ in range(8):
            tracker.update(observation_from_mode("deep", confidence=0.9))
        nearby = PreferenceObservation(
            kind="nearby",
            values=np.clip(tracker.state[: len(ATTR_COLUMNS)] + 0.05, 0.0, 1.0),
            dims=ATTR_DIMS,
            confidence=1.0,
        )
        self.assertTrue(tracker.update(nearby))

    def test_plausible_observations_are_accepted(self):
        tracker = PreferenceTracker()
        self.assertTrue(tracker.update(observation_from_mode("deep", confidence=0.8)))
        self.assertTrue(tracker.update(observation_from_mode("deep", confidence=0.8)))

    def test_step_advances_time_once_for_a_batch(self):
        batched = PreferenceTracker()
        batched.step([observation_from_mode("deep"), observation_from_pace("slow")])

        sequential = PreferenceTracker()
        sequential.predict()
        sequential.update(observation_from_mode("deep"), advance=False)
        sequential.update(observation_from_pace("slow"), advance=False)

        np.testing.assert_allclose(batched.state, sequential.state, atol=1e-12)
        np.testing.assert_allclose(batched.covariance, sequential.covariance, atol=1e-12)

    def test_repeated_evidence_increases_confidence_without_collapse(self):
        tracker = PreferenceTracker()
        confidences = []
        for _ in range(12):
            tracker.update(observation_from_mode("deep", confidence=0.9))
            confidences.append(tracker.confidence())
        self.assertGreater(confidences[-1], confidences[0])
        self.assertLessEqual(confidences[-1], 1.0)
        self.assertGreaterEqual(
            float(np.linalg.eigvalsh(tracker.covariance).min()), tracker.min_variance - 1e-9
        )


class WeightMappingTests(unittest.TestCase):
    def test_cultural_observations_raise_cultural_weight(self):
        tracker = PreferenceTracker()
        baseline_weight = float(tracker.score_weights("balanced")[0])
        for _ in range(8):
            tracker.update(observation_from_mode("deep", confidence=0.85))
        self.assertGreater(float(tracker.state[0]), 0.6)
        self.assertGreater(float(tracker.score_weights("balanced")[0]), baseline_weight)

    def test_uncertain_filter_defers_to_the_mode_baseline(self):
        uncertain = PreferenceTracker()
        uncertain.covariance = np.eye(len(PREFERENCE_DIMS)) * uncertain.max_variance
        uncertain.state = np.zeros(len(PREFERENCE_DIMS))

        confident = PreferenceTracker()
        confident.covariance = np.eye(len(PREFERENCE_DIMS)) * confident.min_variance
        confident.state = np.zeros(len(PREFERENCE_DIMS))

        from tripsense.core.preference import MODE_WEIGHTS

        target = MODE_WEIGHTS["deep"]
        uncertain_gap = float(np.abs(uncertain.score_weights("deep") - target).sum())
        confident_gap = float(np.abs(confident.score_weights("deep") - target).sum())
        self.assertLess(uncertain_gap, confident_gap)

    def test_weights_preserve_baseline_scale_and_stay_positive(self):
        tracker = PreferenceTracker()
        for _ in range(5):
            tracker.update(observation_from_mode("photo", confidence=0.8))
        from tripsense.core.preference import MODE_WEIGHTS

        weights = tracker.score_weights("photo")
        self.assertAlmostEqual(
            float(weights.sum()), float(MODE_WEIGHTS["balanced"].sum()), places=6
        )
        self.assertGreater(float(weights.min()), 0.0)

    def test_fixed_blend_override_is_honoured(self):
        tracker = PreferenceTracker()
        adaptive = tracker.score_weights("deep")
        forced = tracker.score_weights("deep", blend=1.0)
        self.assertFalse(np.allclose(adaptive, forced))


class PaceAndSerializationTests(unittest.TestCase):
    def test_dwell_ratio_slows_pace_when_user_stays_longer(self):
        tracker = PreferenceTracker()
        tracker.update(observation_from_dwell_ratio(120, 60, confidence=0.8))
        self.assertEqual(tracker.pace_hint(), "slow")

    def test_dwell_ratio_speeds_pace_when_user_leaves_early(self):
        tracker = PreferenceTracker()
        for _ in range(3):
            tracker.update(observation_from_dwell_ratio(30, 90, confidence=0.8))
        self.assertEqual(tracker.pace_hint(), "fast")
        self.assertEqual(observation_from_dwell_ratio(30, 90).dims, PACE_DIMS)

    def test_serialization_roundtrip_preserves_state_and_covariance(self):
        tracker = PreferenceTracker()
        tracker.observe_intent("photo", "fast")
        tracker.update(observation_from_poi_attributes([0.8, 0.2, 0.1, 0.9, 0.1, 0.3]))

        restored = PreferenceTracker.from_dict(tracker.to_dict())

        np.testing.assert_allclose(tracker.state, restored.state, atol=1e-5)
        np.testing.assert_allclose(tracker.covariance, restored.covariance, atol=1e-5)

    def test_legacy_diagonal_payload_is_still_accepted(self):
        legacy = {
            "state": {name: 0.4 for name in PREFERENCE_DIMS},
            "variance": {name: 0.2 for name in PREFERENCE_DIMS},
        }
        restored = PreferenceTracker.from_dict(legacy)
        np.testing.assert_allclose(restored.state, np.full(len(PREFERENCE_DIMS), 0.4))
        np.testing.assert_allclose(
            np.diag(restored.covariance), np.full(len(PREFERENCE_DIMS), 0.2)
        )

    def test_empty_payload_returns_prior(self):
        restored = PreferenceTracker.from_dict(None)
        np.testing.assert_allclose(restored.state, PreferenceTracker().state)


if __name__ == "__main__":
    unittest.main()
