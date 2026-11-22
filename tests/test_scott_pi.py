"""Independent arithmetic checks for binary Scott's pi."""
import math
import unittest

from ExamReview.review_helpers import scott_pi


class ScottPiTests(unittest.TestCase):
    def test_balanced_perfect_and_opposite_ratings(self):
        self.assertEqual(scott_pi([0, 1], [0, 1]), 1)
        self.assertEqual(scott_pi([0, 1], [1, 0]), -1)

    def test_pooled_marginals_differ_from_cohen_baseline(self):
        # Observed .5; pooled positive .5; expected .5; pi = 0.
        # Cohen instead expects .375 and gives kappa = .2.
        self.assertEqual(scott_pi([1, 1, 1, 0], [1, 0, 0, 0]), 0)
        self.assertAlmostEqual(scott_pi([1, 1, 1, 0], [1, 1, 0, 0]), 7 / 15)

    def test_empty_and_constant_category_are_undefined(self):
        self.assertTrue(math.isnan(scott_pi([], [])))
        self.assertTrue(math.isnan(scott_pi([1, 1], [1, 1])))

    def test_rejects_unpaired_or_nonbinary_values(self):
        for a, b in [([1], []), ([1, 2], [1, 0]), ([[1]], [[1]]), ([None], [0])]:
            with self.assertRaises(ValueError):
                scott_pi(a, b)
