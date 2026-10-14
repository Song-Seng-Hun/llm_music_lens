import unittest
from beat_eval import event_score, aggregate


class BeatEvaluationTests(unittest.TestCase):
    # Failure: duplicate predictions receive credit for the same reference.
    def test_duplicates_and_unmatched_counts(self):
        score = event_score([1, 2, 3], [1, 1, 2, 4])
        self.assertEqual((score["true_positive"], score["false_positive"], score["false_negative"]), (2, 2, 1))
        self.assertAlmostEqual(score["f1"], 4 / 7)

    # Failure: nearest-first choices reduce maximum one-to-one match count.
    def test_earliest_matching_and_input_order(self):
        score = event_score([1.1, 1], [1.17, 1.06])
        self.assertEqual(score["true_positive"], 2)
        self.assertEqual([(m["reference_index"], m["prediction_index"]) for m in score["matches"]], [(1, 1), (0, 0)])

    # Failure: scoring widens the frozen tolerance or ignores silence errors.
    def test_fixed_boundary_empty_and_invalid(self):
        self.assertEqual(event_score([0], [.07])["true_positive"], 1)
        self.assertEqual(event_score([0], [.070001])["true_positive"], 0)
        self.assertEqual(event_score([], [1])["false_positive"], 1)
        self.assertEqual(event_score([1], [])["false_negative"], 1)
        self.assertIsNone(event_score([], [])["f1"])
        for times in ([float("nan")], [-1], [True]):
            with self.assertRaises(ValueError):
                event_score(times, [])

    # Failure: micro scores drop unmatched tracks or silently equate absent truth.
    def test_aggregation_preserves_denominators(self):
        result = aggregate([event_score([1], [1]), event_score([2, 3], []), None, event_score([], [])])
        self.assertEqual((result["true_positive"], result["false_negative"], result["scored_tracks"]), (1, 2, 3))
        self.assertEqual(result["micro_f1"], .5)
        self.assertEqual(result["macro_f1"], .5)
        self.assertEqual(result["undefined_f1_tracks"], 1)


if __name__ == "__main__":
    unittest.main()
