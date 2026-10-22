# -*- coding: utf-8 -*-
import unittest

from exambank.normalize import (ZWNJ, dedupe_key, normalize_bio_notation, normalize_label,
                                normalize_text, text_hash)


class Chars(unittest.TestCase):
    def test_yeh_kaf(self):
        self.assertEqual(normalize_text("كدام يك از ياخته‌ها"), "کدام یک از یاخته‌ها")

    def test_alef_maqsura(self):
        self.assertEqual(normalize_text("هدى"), "هدی")

    def test_digits_all_scripts(self):
        self.assertEqual(normalize_text("۱۲۳ ٤٥٦ 789"), "123 456 789")

    def test_arabic_decimal_and_percent(self):
        self.assertEqual(normalize_text("۳٫۵٪"), "3.5%")

    def test_tatweel_and_diacritics(self):
        self.assertEqual(normalize_text("ســلول"), "سلول")
        self.assertEqual(normalize_text("سَلُول"), "سلول")

    def test_whitespace(self):
        self.assertEqual(normalize_text("  یاخته \n\t  قرمز\u00a0 "), "یاخته قرمز")

    def test_invisible_marks_removed(self):
        self.assertEqual(normalize_text("\u200fیاخته\u200e"), "یاخته")

    def test_presentation_forms(self):
        self.assertEqual(normalize_text("ﻻ"), "لا")

    def test_empty(self):
        self.assertEqual(normalize_text(None), "")
        self.assertEqual(normalize_text(""), "")

    def test_keep_newlines(self):
        self.assertEqual(normalize_text("الف  )\n\n\n\nب", keep_newlines=True), "الف )\n\nب")


class Zwnj(unittest.TestCase):
    def test_mi_prefix(self):
        self.assertEqual(normalize_text("می شود و نمی شود"), "می" + ZWNJ + "شود و نمی" + ZWNJ + "شود")

    def test_mi_not_inside_word(self):
        # «امی» نباید تغییر کند
        self.assertEqual(normalize_text("امی شد"), "امی شد")

    def test_plural_suffix(self):
        self.assertEqual(normalize_text("یاخته ها و ژن های"), "یاخته" + ZWNJ + "ها و ژن" + ZWNJ + "های")

    def test_comparative(self):
        self.assertEqual(normalize_text("بزرگ تر و بزرگ ترین"), "بزرگ" + ZWNJ + "تر و بزرگ" + ZWNJ + "ترین")

    def test_ye_after_he(self):
        self.assertEqual(normalize_text("گویچه ی قرمز"), "گویچه" + ZWNJ + "ی قرمز")

    def test_spaces_around_zwnj_removed(self):
        self.assertEqual(normalize_text("یاخته " + ZWNJ + " ها"), "یاخته" + ZWNJ + "ها")

    def test_multiple_and_stray_zwnj(self):
        self.assertEqual(normalize_text("می" + ZWNJ * 3 + "شود"), "می" + ZWNJ + "شود")
        self.assertEqual(normalize_text("abc" + ZWNJ + "def"), "abcdef")
        self.assertEqual(normalize_text("یاخته" + ZWNJ), "یاخته")

    def test_existing_correct_zwnj_kept(self):
        s = "یاخته" + ZWNJ + "های"
        self.assertEqual(normalize_text(s), s)

    def test_standalone_word_not_glued(self):
        # «ها» که به حرف فارسی بچسبد در ادامه‌ی کلمه است و نباید با فاصله خراب شود
        self.assertEqual(normalize_text("هایپوتونیک"), "هایپوتونیک")


class BioNotation(unittest.TestCase):
    def test_ions(self):
        self.assertEqual(normalize_bio_notation("Na+ و K+ و Ca2+ و Mg2+"), "Na⁺ و K⁺ و Ca²⁺ و Mg²⁺")
        self.assertEqual(normalize_bio_notation("Cl- و HCO3-"), "Cl⁻ و HCO₃⁻")
        self.assertEqual(normalize_bio_notation("H+"), "H⁺")

    def test_formulas(self):
        self.assertEqual(normalize_bio_notation("CO2 و O2 و H2O و H2O2"), "CO₂ و O₂ و H₂O و H₂O₂")
        self.assertEqual(normalize_bio_notation("C6H12O6"), "C₆H₁₂O₆")

    def test_whitelist_only(self):
        for s in ["B12", "F2", "P1", "ATP", "mRNA", "Na-K", "X-Y"]:
            self.assertEqual(normalize_bio_notation(s), s, s)

    def test_formula_inside_persian(self):
        self.assertEqual(normalize_text("مقدار CO2 خون"), "مقدار CO₂ خون")

    def test_caret_digits_and_signs(self):
        self.assertEqual(normalize_bio_notation("Na^+ Ca^2+ Ca^{2+} PO4^3-"), "Na⁺ Ca²⁺ Ca²⁺ PO4³⁻")

    def test_genotype_letters_use_braces(self):
        self.assertEqual(normalize_bio_notation("X^A X^a"), "X^{A} X^{a}")
        self.assertEqual(normalize_bio_notation("X^{A}Y"), "X^{A}Y")
        self.assertEqual(normalize_bio_notation("I^A I^B"), "I^{A} I^{B}")

    def test_subscripts(self):
        self.assertEqual(normalize_bio_notation("CO_2 H_{2}O"), "CO₂ H₂O")
        self.assertEqual(normalize_bio_notation("F_{n}"), "F_{n}")

    def test_idempotent(self):
        for s in ["Na+ Ca2+ CO2 X^A X^a", "می شود CO2", "۱۲۳ ك ي"]:
            once = normalize_text(s)
            self.assertEqual(normalize_text(once), once)

    def test_unicode_superscripts_not_destroyed(self):
        self.assertEqual(normalize_text("Na⁺ CO₂"), "Na⁺ CO₂")


class Labels(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(normalize_label("۳)"), "3")
        self.assertEqual(normalize_label(" ب. "), "ب")
        self.assertIsNone(None if normalize_label(None) == "" else 1)


class Dedupe(unittest.TestCase):
    def test_same_after_normalization(self):
        a = "گویچه‌ی قرمز بالغ، هسته ندارد."
        b = "گویچهٔ قرمز بالغ هسته ندارد"
        c = "گويچه ی قرمز بالغ هسته ندارد!"
        self.assertEqual(text_hash(a.replace("ٔ", "")), text_hash(c))
        self.assertEqual(dedupe_key(c), dedupe_key("گویچه ی قرمز بالغ هسته ندارد"))

    def test_different(self):
        self.assertNotEqual(text_hash("هسته دارد"), text_hash("هسته ندارد"))

    def test_digits_script_irrelevant(self):
        self.assertEqual(text_hash("۲ مورد"), text_hash("2 مورد"))


if __name__ == "__main__":
    unittest.main()
