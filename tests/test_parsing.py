# -*- coding: utf-8 -*-
import unittest

from exambank.parsing import (contains_quote, novel_token_ratio, options_look_like, parse_combo_option,
                              parse_count_option, polarity_hint)
from exambank.truth_rules import ComboOption

L = ["الف", "ب", "ج", "د"]


class Combo(unittest.TestCase):
    def test_sets(self):
        self.assertEqual(parse_combo_option("الف و ج", L), ComboOption("set", ("الف", "ج")))
        self.assertEqual(parse_combo_option("فقط ب", L), ComboOption("set", ("ب",)))
        self.assertEqual(parse_combo_option("الف، ب و د", L), ComboOption("set", ("الف", "ب", "د")))
        self.assertEqual(parse_combo_option("(ب) و (د)", L), ComboOption("set", ("ب", "د")))
        self.assertEqual(parse_combo_option("موارد الف و ب", L), ComboOption("set", ("الف", "ب")))

    def test_order_is_canonical(self):
        self.assertEqual(parse_combo_option("د و الف", L), ComboOption("set", ("الف", "د")))

    def test_all_none(self):
        self.assertEqual(parse_combo_option("همه‌ی موارد", L), ComboOption("all"))
        self.assertEqual(parse_combo_option("همه", L), ComboOption("all"))
        self.assertEqual(parse_combo_option("هیچ‌کدام", L), ComboOption("none"))
        self.assertEqual(parse_combo_option("هیچ یک", L), ComboOption("none"))

    def test_not_certain_is_none(self):
        for t in ("الف و ه", "الف و الف", "سه مورد", "", "الف تا ج", "همه به جز ب"):
            self.assertIsNone(parse_combo_option(t, L), t)

    def test_other_labels(self):
        self.assertEqual(parse_combo_option("الف و پ", ["الف", "ب", "پ", "ت"]), ComboOption("set", ("الف", "پ")))


class Count(unittest.TestCase):
    def test_ok(self):
        for t, v in (("۲", 2), ("دو مورد", 2), ("فقط سه مورد", 3), ("صفر", 0), ("هیچ‌کدام", 0), ("4", 4),
                     ("۱ مورد", 1)):
            self.assertEqual(parse_count_option(t), v, t)

    def test_none(self):
        for t in ("الف", "الف و ب", "بیشتر از دو", "", "همه"):
            self.assertIsNone(parse_count_option(t), t)

    def test_options_look_like(self):
        ok = [{"text_original": x} for x in ("۱", "۲", "۳", "۴")]
        bad = [{"text_original": x} for x in ("الف و ب", "۲", "۳", "۴")]
        self.assertTrue(options_look_like("count_items", ok))
        self.assertFalse(options_look_like("count_items", bad))
        self.assertIsNone(options_look_like("completion", ok))


class Polarity(unittest.TestCase):
    def test_negative(self):
        for s in ("کدام عبارت نادرست است؟", "کدام گزینه غلط است؟", "همه‌ی موارد به‌جز مورد ... صحیح‌اند", "چند مورد نادرست است؟",
                  "کدام عبارت درباره‌ی ... نیست؟", "کدام موارد صحیح نیستند؟"):
            self.assertEqual(polarity_hint(s), "negative", s)

    def test_positive(self):
        for s in ("کدام گزینه صحیح است؟", "کدام عبارت درست است؟", "کدام موارد درست‌اند؟", "چند مورد درست می‌باشد؟"):
            self.assertEqual(polarity_hint(s), "positive", s)

    def test_nadorost_not_positive(self):
        # «نادرست است» شامل «درست است» است و نباید مثبت حساب شود
        self.assertEqual(polarity_hint("کدام نادرست است؟"), "negative")

    def test_unknown_or_ambiguous(self):
        self.assertIsNone(polarity_hint("کدام یاخته فاقد هسته است؟"))
        self.assertIsNone(polarity_hint("کدام درست است ولی گزینه‌ی دیگر نیست؟"))
        self.assertIsNone(polarity_hint(""))


class Novelty(unittest.TestCase):
    def test_low_when_composed_from_sources(self):
        r = novel_token_ratio("گویچه‌ی قرمز بالغ فاقد هسته است", "کدام یاخته فاقد هسته است؟", "گویچه‌ی قرمز بالغ")
        self.assertEqual(r, 0.0)

    def test_high_when_adding_facts(self):
        r = novel_token_ratio("گویچه قرمز به دلیل نداشتن میتوکندری هیچ‌گاه هسته ندارد", "کدام یاخته فاقد هسته است؟",
                              "گویچه قرمز")
        self.assertGreater(r, 0.5)

    def test_empty(self):
        self.assertEqual(novel_token_ratio("", "x"), 0.0)


class Quote(unittest.TestCase):
    def test_insensitive_to_punct_and_chars(self):
        self.assertTrue(contains_quote("گزینه‌ي ۳ نادرست است، چون ...", "گزینه ی 3 نادرست است"))
        self.assertFalse(contains_quote("گزینه ۳ درست است", "گزینه ۳ نادرست است"))
        self.assertFalse(contains_quote("متن", ""))
        self.assertFalse(contains_quote("", "متن"))


if __name__ == "__main__":
    unittest.main()
