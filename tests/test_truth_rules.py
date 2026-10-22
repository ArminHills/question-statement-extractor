# -*- coding: utf-8 -*-
import unittest

from exambank.truth_rules import ComboOption, derive_truth, CONFLICT

OPTS = ["1", "2", "3", "4"]
ITEMS = ["الف", "ب", "ج", "د"]


def truths(results):
    return [r.truth for r in results]


def run(**kw):
    base = dict(answer_status="official", explicit=None)
    base.update(kw)
    return derive_truth(**base)


class SingleBestStatement(unittest.TestCase):
    def test_positive(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["2"], targets=OPTS)
        self.assertEqual(truths(r), [False, True, False, False])
        self.assertTrue(all(x.basis == "key_derived" and not x.needs_review for x in r))

    def test_negative_nadorost(self):
        # «کدام عبارت نادرست است؟» کلید=۳ ← گزینه‌ی ۳ غلط، بقیه صحیح
        r = run(structure="single_best_statement", polarity="negative", correct=["3"], targets=OPTS)
        self.assertEqual(truths(r), [True, True, False, True])

    def test_negative_beh_joz(self):
        r = run(structure="single_best_statement", polarity="negative", correct=["1"], targets=OPTS)
        self.assertEqual(truths(r), [False, True, True, True])

    def test_internal_negation_does_not_flip(self):
        # مثال ۱ prompt: «کدام نادرست است؟» گزینه‌ی «گویچه‌ی قرمز بالغ هسته دارد» کلید است.
        # گزاره‌ی این گزینه غلط است (polarity=negative)، حتی اگر متنش نفی داشته باشد کد چیزی نمی‌داند.
        r = run(structure="single_best_statement", polarity="negative", correct=["2"], targets=OPTS)
        self.assertIs(r[1].truth, False)
        # مثال ۲: «کدام یاخته فاقد هسته است؟» (completion، مثبت) کلید=۴ ← گزینه‌ی ۴ صحیح
        r = run(structure="completion", polarity="positive", correct=["4"], targets=OPTS)
        self.assertEqual(truths(r), [False, False, False, True])

    def test_two_keys_positive(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["1", "3"],
                targets=OPTS, answer_status="multiple_keys")
        self.assertEqual(truths(r), [True, False, True, False])

    def test_two_keys_negative(self):
        r = run(structure="completion", polarity="negative", correct=["1", "3"],
                targets=OPTS, answer_status="multiple_keys")
        self.assertEqual(truths(r), [False, True, False, True])

    def test_annulled(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["2"],
                targets=OPTS, answer_status="annulled")
        self.assertEqual(truths(r), [None] * 4)
        self.assertTrue(all(x.basis == "unresolved" and "answer_status_annulled" in x.review_reasons for x in r))

    def test_disputed_even_with_explicit(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["2"], targets=OPTS,
                answer_status="disputed", explicit={"1": (True, "...")})
        self.assertEqual(truths(r), [None] * 4)

    def test_missing_key(self):
        r = run(structure="single_best_statement", polarity="positive", correct=[], targets=OPTS,
                answer_status="missing")
        self.assertEqual(truths(r), [None] * 4)
        self.assertIn("no_key", r[0].review_reasons)

    def test_key_not_in_options(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["9"], targets=OPTS)
        self.assertEqual(truths(r), [None] * 4)
        self.assertIn("key_not_in_options", r[0].review_reasons)

    def test_polarity_missing(self):
        r = run(structure="single_best_statement", polarity=None, correct=["2"], targets=OPTS)
        self.assertEqual(truths(r), [None] * 4)

    def test_unsupported_structure(self):
        r = run(structure="calculation", polarity="positive", correct=["2"], targets=OPTS)
        self.assertEqual(truths(r), [None] * 4)


class ComboItems(unittest.TestCase):
    COMBO = {
        "1": ComboOption("set", ("الف", "ج")),
        "2": ComboOption("set", ("ب",)),          # «فقط ب»
        "3": ComboOption("all"),                  # «همه»
        "4": ComboOption("none"),                 # «هیچ‌کدام»
    }

    def go(self, polarity, key):
        return run(structure="combo_items", polarity=polarity, correct=[key], targets=ITEMS, combo=self.COMBO)

    def test_set_positive(self):
        self.assertEqual(truths(self.go("positive", "1")), [True, False, True, False])

    def test_set_negative(self):
        self.assertEqual(truths(self.go("negative", "1")), [False, True, False, True])

    def test_only(self):
        self.assertEqual(truths(self.go("positive", "2")), [False, True, False, False])

    def test_all(self):
        self.assertEqual(truths(self.go("positive", "3")), [True] * 4)
        self.assertEqual(truths(self.go("negative", "3")), [False] * 4)

    def test_none(self):
        self.assertEqual(truths(self.go("positive", "4")), [False] * 4)
        self.assertEqual(truths(self.go("negative", "4")), [True] * 4)

    def test_two_keys_unresolved(self):
        r = run(structure="combo_items", polarity="positive", correct=["1", "2"], targets=ITEMS,
                combo=self.COMBO, answer_status="multiple_keys")
        self.assertEqual(truths(r), [None] * 4)
        self.assertIn("multiple_keys_combo", r[0].review_reasons)

    def test_unknown_item_in_set(self):
        combo = {"1": ComboOption("set", ("الف", "ه"))}
        r = run(structure="combo_items", polarity="positive", correct=["1"], targets=ITEMS, combo=combo)
        self.assertEqual(truths(r), [None] * 4)

    def test_missing_combo_sets(self):
        r = run(structure="combo_items", polarity="positive", correct=["1"], targets=ITEMS, combo=None)
        self.assertIn("combo_sets_missing", r[0].review_reasons)

    def test_matching_ambiguous(self):
        r = run(structure="matching", polarity="positive", correct=["1"], targets=ITEMS, combo=None)
        self.assertIn("matching_ambiguous", r[0].review_reasons)

    def test_ordering_like_combo(self):
        combo = {"1": ComboOption("set", ("الف", "ب"))}
        r = run(structure="ordering", polarity="positive", correct=["1"], targets=ITEMS, combo=combo)
        self.assertEqual(truths(r), [True, True, False, False])


class CountItems(unittest.TestCase):
    COUNTS = {"1": 0, "2": 1, "3": 2, "4": 4}

    def go(self, polarity, key, explicit=None, n=4):
        return run(structure="count_items", polarity=polarity, correct=[key], targets=ITEMS[:n],
                   counts=self.COUNTS, explicit=explicit)

    def test_c_equals_n_positive(self):
        self.assertEqual(truths(self.go("positive", "4")), [True] * 4)

    def test_c_equals_zero_positive(self):
        self.assertEqual(truths(self.go("positive", "1")), [False] * 4)

    def test_c_equals_n_negative(self):
        # «چند مورد نادرست است؟» ۴ ← همه غلط
        self.assertEqual(truths(self.go("negative", "4")), [False] * 4)

    def test_c_equals_zero_negative(self):
        self.assertEqual(truths(self.go("negative", "1")), [True] * 4)

    def test_middle_is_null(self):
        r = self.go("positive", "3")
        self.assertEqual(truths(r), [None] * 4)
        self.assertTrue(all(x.basis == "unresolved" and x.needs_review for x in r))

    def test_middle_with_explanation_deduces_rest(self):
        # c=2، دو مورد صریحاً صحیح ← بقیه قطعاً غلط
        r = self.go("positive", "3", explicit={"الف": (True, "x"), "ب": (True, "y")})
        self.assertEqual(truths(r), [True, True, False, False])
        self.assertEqual([x.basis for x in r], ["explanation", "explanation", "key_derived", "key_derived"])
        self.assertFalse(any(x.needs_review for x in r))

    def test_middle_with_explanation_all_remaining_true(self):
        # c=2، دو مورد صریحاً غلط ← بقیه قطعاً صحیح
        r = self.go("positive", "3", explicit={"ج": (False, "x"), "د": (False, "y")})
        self.assertEqual(truths(r), [True, True, False, False])

    def test_middle_partial_explanation_not_enough(self):
        r = self.go("positive", "3", explicit={"الف": (True, "x")})
        self.assertEqual(r[0].truth, True)
        self.assertEqual(truths(r)[1:], [None, None, None])

    def test_explanations_contradict_count(self):
        r = self.go("positive", "2", explicit={"الف": (True, "x"), "ب": (True, "y"),
                                              "ج": (False, "z"), "د": (False, "w")})
        self.assertTrue(all(CONFLICT in x.review_reasons for x in r))

    def test_count_out_of_range(self):
        r = run(structure="count_items", polarity="positive", correct=["1"], targets=ITEMS[:2],
                counts={"1": 3})
        self.assertEqual(truths(r), [None, None])
        self.assertIn("count_out_of_range", r[0].review_reasons)


class ExplanationPriority(unittest.TestCase):
    def test_explanation_agrees(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["2"], targets=OPTS,
                explicit={"2": (True, "گزینه‌ی ۲ درست است")})
        self.assertEqual(r[1].basis, "explanation")
        self.assertEqual(r[1].evidence, "گزینه‌ی ۲ درست است")
        self.assertFalse(r[1].needs_review)
        self.assertEqual(r[0].basis, "key_derived")

    def test_conflict_flagged(self):
        r = run(structure="single_best_statement", polarity="positive", correct=["2"], targets=OPTS,
                explicit={"2": (False, "غلط است")})
        self.assertIn(CONFLICT, r[1].review_reasons)
        self.assertIs(r[1].truth, False)  # حکم صریح نگه داشته می‌شود و بازبین تصمیم می‌گیرد

    def test_explanation_fills_missing_key(self):
        r = run(structure="single_best_statement", polarity="positive", correct=[], targets=OPTS,
                answer_status="missing", explicit={"3": (True, "ص")})
        self.assertIs(r[2].truth, True)
        self.assertEqual(r[2].basis, "explanation")
        self.assertIsNone(r[0].truth)


if __name__ == "__main__":
    unittest.main()
