#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for pick.py — the scoring engine.

Stdlib only, Python 3.6+. Run:

    python3 -m unittest discover -s tests -v

Covers the behaviours that are easy to break silently:
  * ROC weights sum to 1 and decrease monotonically
  * normalisation ends at 0 and 1 in the right direction
  * score == sum(normalised * weight) * 100
  * score contributions sum back to the score (the 3.1 table invariant)
  * attributes with no spread are neutralised at 0.5
  * flip_factor really does zero the gap
  * price never moves the score
  * gated / incomplete candidates stay out of the ranking
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "product-pick", "scripts")
PICK_PY = os.path.join(SCRIPTS, "pick.py")
sys.path.insert(0, SCRIPTS)

import pick  # noqa: E402


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


class TestRocWeights(unittest.TestCase):
    def test_sums_to_one(self):
        for n in range(1, 9):
            self.assertTrue(close(sum(pick.roc_weights(n)), 1.0), "n=%d" % n)

    def test_monotone_decreasing(self):
        for n in range(2, 9):
            ws = pick.roc_weights(n)
            for i in range(len(ws) - 1):
                self.assertGreater(ws[i], ws[i + 1], "n=%d pos=%d" % (n, i))

    def test_known_values_n5(self):
        # Barron & Barrett (1996): 45.7 / 25.7 / 15.7 / 9.0 / 4.0
        ws = pick.roc_weights(5)
        expected = [0.4567, 0.2567, 0.1567, 0.0900, 0.0400]
        for got, want in zip(ws, expected):
            self.assertAlmostEqual(got, want, places=3)

    def test_top_weight_falls_as_n_grows(self):
        prev = 1.0
        for n in range(2, 9):
            top = pick.roc_weights(n)[0]
            self.assertLess(top, prev)
            prev = top


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.attrs = [("weight", "-"), ("power", "+")]
        self.rows = [("A", [1000, 700]), ("B", [1200, 800]), ("C", [800, 900])]

    def test_endpoints(self):
        norm, stats = pick.normalize(self.rows, self.attrs)
        # weight: lower is better -> C (800) == 1.0, B (1200) == 0.0
        self.assertTrue(close(norm["C"][0], 1.0))
        self.assertTrue(close(norm["B"][0], 0.0))
        # power: higher is better -> C (900) == 1.0, A (700) == 0.0
        self.assertTrue(close(norm["C"][1], 1.0))
        self.assertTrue(close(norm["A"][1], 0.0))

    def test_range_within_0_1(self):
        norm, _ = pick.normalize(self.rows, self.attrs)
        for name, vals in norm.items():
            for v in vals:
                self.assertGreaterEqual(v, 0.0, name)
                self.assertLessEqual(v, 1.0, name)

    def test_stats_reported(self):
        _, stats = pick.normalize(self.rows, self.attrs)
        self.assertEqual(stats[0]["min"], 800)
        self.assertEqual(stats[0]["max"], 1200)
        self.assertEqual(stats[0]["span"], 400)

    def test_flat_attribute_is_neutral(self):
        rows = [("A", [100, 5]), ("B", [200, 5])]
        norm, stats = pick.normalize(rows, [("x", "+"), ("flat", "+")])
        self.assertEqual(stats[1]["span"], 0)
        self.assertTrue(close(norm["A"][1], 0.5))
        self.assertTrue(close(norm["B"][1], 0.5))


class TestScores(unittest.TestCase):
    def test_score_formula(self):
        norm = {"A": [1.0, 0.0], "B": [0.0, 1.0]}
        scores = pick.scores_of(norm, [0.7, 0.3])
        self.assertTrue(close(scores["A"], 70.0))
        self.assertTrue(close(scores["B"], 30.0))

    def test_contribution_sums_to_score(self):
        """The invariant behind the 3.1 score-contribution table."""
        attrs = [("weight", "-"), ("power", "+"), ("price_level", "-")]
        rows = [
            ("A", [1000, 700, 3]),
            ("B", [1200, 800, 5]),
            ("C", [800, 900, 4]),
        ]
        weights = [0.5, 0.3, 0.2]
        norm, _ = pick.normalize(rows, attrs)
        scores = pick.scores_of(norm, weights)
        for name, _vals in rows:
            contrib = sum(norm[name][i] * weights[i] * 100 for i in range(len(attrs)))
            self.assertAlmostEqual(contrib, scores[name], places=9, msg=name)

    def test_identical_products_tie(self):
        rows = [("A", [100, 5]), ("B", [100, 5])]
        norm, _ = pick.normalize(rows, [("x", "+"), ("y", "-")])
        scores = pick.scores_of(norm, [0.6, 0.4])
        self.assertAlmostEqual(scores["A"], scores["B"], places=9)

    def test_full_and_zero(self):
        rows = [("best", [1, 1]), ("worst", [0, 0])]
        norm, _ = pick.normalize(rows, [("x", "+"), ("y", "+")])
        scores = pick.scores_of(norm, [0.5, 0.5])
        self.assertTrue(close(scores["best"], 100.0))
        self.assertTrue(close(scores["worst"], 0.0))


class TestSensitivity(unittest.TestCase):
    def test_flip_factor_zeroes_the_gap(self):
        weights = [0.5, 0.3, 0.2]
        d = [0.6, -0.2, 0.1]  # winner leads on attr 0
        f = pick.flip_factor(weights, d, 0)
        self.assertIsNotNone(f)
        w2 = list(weights)
        w2[0] *= f
        total = sum(w2)
        w2 = [w / total for w in w2]
        gap = sum(w2[i] * d[i] for i in range(len(d)))
        self.assertAlmostEqual(gap, 0.0, places=9)

    def test_no_flip_when_no_gap_on_that_attr(self):
        self.assertIsNone(pick.flip_factor([0.5, 0.5], [0.0, 0.4], 0))

    def test_zero_weight_has_no_flip_factor(self):
        self.assertIsNone(pick.flip_factor([1.0, 0.0], [0.5, -0.5], 1))

    def test_negative_worst_case_moves_in_adverse_direction(self):
        rows = [("A", [-9.0]), ("B", [-8.0])]
        adjusted = pick.adjust_for_worst_case(rows, [("comfort", "-")], {"A": 0.1, "B": 0.0})
        self.assertEqual(adjusted[0][1][0], -8.1)

    def test_dominant_product_is_never_flipped(self):
        """Winner not worse on any attribute -> no single-weight flip exists."""
        rows = [("A", [10, 10]), ("B", [5, 5])]
        attrs = [("x", "+"), ("y", "+")]
        norm, _ = pick.normalize(rows, attrs)
        scores = pick.scores_of(norm, [0.5, 0.5])
        sens, best = pick.sens_analysis(attrs, [0.5, 0.5], norm, ["A", "B"], scores)
        self.assertEqual(sens, [])


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        out = subprocess.check_output(
            [sys.executable, PICK_PY, "run"] + list(args),
            stderr=subprocess.STDOUT,
        )
        return out.decode("utf-8")

    def run_json(self, *args):
        out = subprocess.check_output(
            [sys.executable, PICK_PY, "run", "--json"] + list(args),
            stderr=subprocess.STDOUT,
        )
        return json.loads(out.decode("utf-8"))

    BASE = [
        "--attrs", "x:+,y:-",
        "--weights", "60,40",
        "--data", "A:10,5;B:8,3",
    ]

    def test_price_does_not_change_score(self):
        without = self.run_json(*self.BASE)
        with_price = self.run_json(*(self.BASE + ["--price", "A:100,B:200"]))
        self.assertEqual(
            [r["score"] for r in without["scores"]],
            [r["score"] for r in with_price["scores"]],
        )

    def test_contrib_reported_and_sums(self):
        d = self.run_json(*self.BASE)
        self.assertIn("contrib", d)
        for row in d["scores"]:
            self.assertAlmostEqual(
                sum(d["contrib"][row["name"]]), row["score"], places=1
            )

    def test_gated_out_excluded(self):
        d = self.run_json(*(self.BASE + ["--gates", "A:pass;B:fail"]))
        names = [r["name"] for r in d["scores"]]
        self.assertIn("A", names)
        self.assertNotIn("B", names)
        self.assertIn("B", d["gated_out"])

    def test_incomplete_excluded(self):
        d = self.run_json(
            "--attrs", "x:+,y:-", "--weights", "60,40",
            "--data", "A:10,5;B:?,3;C:8,2",
        )
        names = [r["name"] for r in d["scores"]]
        self.assertNotIn("B", names)
        self.assertIn("B", d["incomplete"])

    def test_max_price_filters(self):
        d = self.run_json(
            *(self.BASE + ["--price", "A:300,B:100", "--max-price", "150"])
        )
        self.assertEqual([r["name"] for r in d["scores"]], ["B"])

    def test_price_band_boundary_is_counted_once(self):
        d = self.run_json(
            "--attrs", "x:+", "--weights", "1",
            "--data", "A:1;B:2;C:3",
            "--price", "A:100,B:200,C:300", "--bands", "200",
        )
        self.assertEqual([b["count"] for b in d["bands"]], [1, 2])

    def test_gates_require_explicit_pass_or_fail_for_every_candidate(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_json(
                "--attrs", "x:+", "--weights", "1",
                "--data", "A:1;B:2", "--gates", "A:pass",
            )

    def test_duplicate_price_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_json(
                "--attrs", "x:+", "--weights", "1",
                "--data", "A:1;B:2", "--price", "A:100,A:200",
            )

    def test_zero_weight_is_reported_as_limited_when_winner_is_not_dominant(self):
        d = self.run_json(
            "--attrs", "x:+,y:+", "--weights", "100,0",
            "--data", "A:10,0;B:5,10",
        )
        self.assertEqual(d["verdict"], "limited")

    def test_price_thousands_separator_is_supported(self):
        d = self.run_json(
            "--attrs", "x:+", "--weights", "1",
            "--data", "A:1;B:2", "--price", "A:1,299,B:2,499",
        )
        prices = dict((row["name"], row["price"]) for row in d["scores"])
        self.assertEqual(prices, {"A": 1299.0, "B": 2499.0})

    def test_global_json_flag_is_supported(self):
        out = subprocess.check_output(
            [sys.executable, PICK_PY, "--json", "run"] + self.BASE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(json.loads(out.decode("utf-8"))["attrs"][0]["name"], "x")

    def test_median_imputation_is_opt_in(self):
        strict = self.run_json(
            "--attrs", "x:+,y:+", "--weights", "50,50",
            "--data", "A:1,2;B:2,?;C:3,4",
        )
        self.assertIn("B", strict["incomplete"])
        imputed = self.run_json(
            "--attrs", "x:+,y:+", "--weights", "50,50",
            "--data", "A:1,2;B:2,?;C:3,4", "--impute", "median",
        )
        self.assertNotIn("B", imputed["incomplete"])
        self.assertIn("B", [r["name"] for r in imputed["scores"]])
        self.assertEqual(imputed["imputed"], {"B": {"y": 3.0}})
        self.assertIn("B（y=3）", "".join(imputed["notes"]))

    def test_markdown_contains_contribution_table(self):
        text = self.run_cli(*self.BASE)
        self.assertIn("得分构成", text)
        self.assertIn("该项满分", text)

    def test_packing_volume_regression(self):
        """Regression: two products identical except one attribute.

        The whole gap must land in that single attribute's contribution.
        This is the bug class that produced a phantom 2.76-point difference
        in the sleeping-bag run (mismatched volume definitions).
        """
        d = self.run_json(
            "--attrs", "w:-,v:-",
            "--weights", "50,50",
            "--data", "P:990,6;Q:990,8",
        )
        scores = dict((r["name"], r["score"]) for r in d["scores"])
        self.assertAlmostEqual(scores["Q"] - scores["P"], -50.0, places=6)
        # column-wise: the identical attribute contributes equally, the
        # differing one carries 100% of the gap
        per_col_gap = [p - q for p, q in
                       zip(d["contrib"]["P"], d["contrib"]["Q"])]
        self.assertAlmostEqual(per_col_gap[0], 0.0, places=6)
        self.assertAlmostEqual(per_col_gap[1], 50.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
