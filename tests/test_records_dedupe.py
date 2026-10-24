# -*- coding: utf-8 -*-
import copy
import json
import re
import time
import unittest
from pathlib import Path

from exambank import records
from exambank.dedupe import (CONFLICT, cluster_records, comparable, dedupe_problems, dedupe_statements,
                             is_near_duplicate, similarity)
from exambank.records import (Problem, Question, Statement, json_schema, to_pydantic, validate)
from exambank.schema_md import REASON_CATALOG, render_schema_md

ROOT = Path(__file__).resolve().parent.parent


def stmt(sid="ex_q001_o1", text="گویچه‌ی قرمز بالغ فاقد هسته است", truth=True, year=1402, exam="ex1", qno=1,
         structure="single_best_statement", standalone=True, status="pending", reasons=(), original=None):
    return {
        "schema_version": "1.0", "statement_id": sid, "question_id": f"{exam}_q{qno:03d}",
        "text_original": original if original is not None else text, "text_standalone": text, "truth": truth,
        "truth_basis": "unresolved" if truth is None else "key_derived", "why_false": None, "is_atomic": True,
        "standalone": standalone, "context_dependency": "none", "context_ref": None, "topic_ids": ["t1"],
        "question_type": "textbook_recall", "structure": structure,
        "source": {"exam_id": exam, "year_jalali": year, "group": "g", "question_no": qno, "option_label": "1",
                   "pages": {"question": [1], "answer": [2]}},
        "dedupe": {"canonical_id": sid, "occurrences": 1}, "needs_review": bool(reasons),
        "review_reasons": list(reasons), "review_status": status, "llm_confidence": 0.9}


class RecordsValidator(unittest.TestCase):
    def test_good_and_errors(self):
        s = stmt()
        self.assertEqual(validate(Statement, s), [])
        for mut, frag in (
                (lambda d: d.pop("truth"), "truth: الزامی"),                       # Optional بدون پیش‌فرض = الزامی
                (lambda d: d.update(truth="yes"), "truth"),
                (lambda d: d.update(truth_basis="guess"), "truth_basis"),
                (lambda d: d.update(is_atomic=1), "is_atomic"),
                (lambda d: d.update(llm_confidence="x"), "llm_confidence"),
                (lambda d: d.update(topic_ids="t1"), "topic_ids"),
                (lambda d: d.update(topic_ids=[1]), "topic_ids[0]"),
                (lambda d: d["source"].pop("exam_id"), "source.exam_id"),
                (lambda d: d["source"]["pages"].update(question=["1"]), "source.pages.question[0]"),
                (lambda d: d.update(source=None), "source"),
                (lambda d: d.update(review_status="done"), "review_status"),
                (lambda d: d["source"].update(question_no=True), "question_no")):
            d = copy.deepcopy(s)
            mut(d)
            errs = validate(Statement, d)
            self.assertTrue(any(frag in e for e in errs), (frag, errs))

    def test_optional_and_extra_fields(self):
        s = stmt()
        s["unknown_future_field"] = {"x": 1}
        s["review"] = {"at": "2026", "note": None}
        s["truth_evidence"] = None
        self.assertEqual(validate(Statement, s), [])
        s["polarity"] = "sideways"
        self.assertTrue(validate(Statement, s))
        self.assertEqual(validate(Statement, "x"), ["Statement: باید شیء باشد"])

    def test_manual_basis_allowed(self):
        s = stmt()
        s["truth_basis"] = "manual"
        self.assertEqual(validate(Statement, s), [])

    def test_json_schema(self):
        sch = json_schema(Statement)
        json.dumps(sch)
        self.assertIn("truth", sch["required"])
        self.assertNotIn("truth_evidence", sch["required"])
        self.assertIn("manual", sch["properties"]["truth_basis"]["enum"])
        self.assertEqual(sch["properties"]["source"], {"$ref": "#/$defs/Source", "description": sch["properties"]["source"]["description"]})
        self.assertEqual(sch["$defs"]["Source"]["required"], ["exam_id", "year_jalali", "group", "question_no", "option_label", "pages"])
        self.assertIn("Pages", sch["$defs"])
        self.assertEqual(sch["properties"]["truth"]["anyOf"], [{"type": "boolean"}, {"type": "null"}])
        self.assertTrue(sch["properties"]["truth"]["description"])
        for cls in (Problem, Question):
            json.dumps(json_schema(cls))

    def test_enums_match_constants(self):
        from typing import get_args, get_type_hints
        h = get_type_hints(Statement)
        self.assertEqual(set(get_args(h["structure"])), set(records.STRUCTURES))
        self.assertEqual(set(get_args(h["question_type"])), set(records.QUESTION_TYPES))
        self.assertEqual(set(get_args(h["truth_basis"])), set(records.TRUTH_BASES))
        from exambank.stages.classify import CONTEXTS, QUESTION_TYPES, ROUTES, STRUCTURES
        self.assertEqual((set(STRUCTURES), set(QUESTION_TYPES), set(ROUTES), set(CONTEXTS)),
                         (set(records.STRUCTURES), set(records.QUESTION_TYPES), set(records.ROUTES), set(records.CONTEXTS)))

    def test_pydantic_parity_when_installed(self):
        try:
            import pydantic  # noqa: F401
        except ImportError:
            self.skipTest("pydantic نصب نیست")
        M = to_pydantic(Statement)
        M.model_validate(stmt())
        for bad in ({"truth_basis": "guess"}, {"review_status": "done"}, {"structure": "x"}):
            with self.assertRaises(Exception):
                M.model_validate({**stmt(), **bad})
        d = stmt()
        d.pop("source")
        with self.assertRaises(Exception):
            M.model_validate(d)
        M.model_validate({**stmt(), "future_field": 1})          # فیلد ناشناخته مجاز
        Pm = to_pydantic(Problem)
        self.assertEqual(set(Pm.model_fields), set(records._fields(Problem)))


class SchemaMd(unittest.TestCase):
    def test_contains_every_field_and_reason(self):
        md = render_schema_md()
        for cls in (Statement, Problem, Question):
            for f in records._fields(cls):
                self.assertIn(f"`{f}`", md, f"{cls.__name__}.{f}")
        for code in REASON_CATALOG:
            self.assertIn(f"`{code}`", md)
        self.assertIn("polarity", md)
        self.assertIn("truth_basis", md)
        self.assertIn("نادرست است؟", md)

    def test_every_reason_code_in_source_is_documented(self):
        """هر کدِ review_reasons که در کد پایتون ساخته می‌شود باید در کاتالوگ SCHEMA.md باشد."""
        pats = [r'\.append\("([a-z_0-9]+)"\)', r'fail\("([a-z_0-9]+)"\)', r'_all_unresolved\(targets, "([a-z_0-9]+)"\)',
                r'reasons = \["([a-z_0-9]+)"\]', r'CONFLICT = "([a-z_0-9]+)"', r'STALE = "([a-z_0-9]+)"',
                r'r\.append\("([a-z_0-9]+)"\)']
        files = ["truth_rules.py", "stages/atomize.py", "stages/classify.py", "stages/validate.py", "dedupe.py", "review.py"]
        found = set()
        for f in files:
            src = (ROOT / "exambank" / f).read_text(encoding="utf-8")
            for p in pats:
                found.update(re.findall(p, src))
        found -= {"x"}
        # کدهای دیگرِ غیر review (مثل نام‌های داخلی) نباید در این الگوها باشند؛ هر چه هست باید مستند شود
        missing = sorted(c for c in found if c not in REASON_CATALOG)
        self.assertEqual(missing, [], f"کدهای بدون مستند: {missing}")
        self.assertGreater(len(found), 25)  # الگوها واقعاً چیزی پیدا می‌کنند
        for dyn in ("answer_status_annulled", "answer_status_disputed"):
            self.assertIn(dyn, REASON_CATALOG)


class Similarity(unittest.TestCase):
    def test_guards(self):
        self.assertFalse(is_near_duplicate("گویچه قرمز بالغ هسته دارد", "گویچه قرمز بالغ هسته ندارد", 80))
        self.assertFalse(is_near_duplicate("یاخته دارای ۲ هسته است", "یاخته دارای ۳ هسته است", 80))
        self.assertFalse(is_near_duplicate("الف از ب بزرگ‌تر است", "ب از الف بزرگ‌تر است", 80))
        self.assertFalse(is_near_duplicate("این مورد درست است", "این مورد نیست", 50))
        self.assertTrue(is_near_duplicate("هورمون تیروکسین از غده‌ی تیروئید ترشح می‌شود",
                                          "هورمون تیروکسین از غده تیروئید ترشح می‌شود", 85))
        self.assertTrue(is_near_duplicate("متن یکسان.", "متن یکسان", 99))
        self.assertGreater(similarity("abc", "abc"), 99)

    def test_threshold_is_configurable(self):
        a, b = "هورمون تیروکسین از غده‌ی تیروئید ترشح می‌شود", "هورمون انسولین از غده‌ی لوزالمعده ترشح می‌شود"
        self.assertFalse(is_near_duplicate(a, b, 90))
        self.assertTrue(is_near_duplicate(a, b, 40))


class StatementDedupe(unittest.TestCase):
    def test_exact_across_years(self):
        ss = [stmt("b_q001_o1", year=1403, exam="b"), stmt("a_q007_o2", year=1401, exam="a", qno=7),
              stmt("c_q003_o1", year=1402, exam="c", qno=3, text="گویچه‌ی قرمز بالغ، فاقد هسته است.")]
        info = dedupe_statements(ss)
        for s in ss:
            self.assertEqual(s["dedupe"]["canonical_id"], "a_q007_o2")      # قدیمی‌ترین سال
            self.assertEqual((s["dedupe"]["occurrences"], s["dedupe"]["years"]), (3, [1401, 1402, 1403]))
            self.assertEqual(s["dedupe"]["match"], "exact")
        self.assertEqual((info["clusters"], info["duplicated_statements"], info["truth_conflict_clusters"]), (1, 3, 0))
        self.assertEqual(len(ss), 3)  # هیچ رکوردی پاک نشد

    def test_fuzzy_marked(self):
        ss = [stmt("a_q001_o1", "هورمون تیروکسین از غده‌ی تیروئید ترشح می‌شود", year=1401, exam="a"),
              stmt("b_q001_o1", "هورمون تیروکسین از غده تیروئید ترشح می‌شود", year=1402, exam="b")]
        dedupe_statements(ss, 85)
        self.assertEqual([s["dedupe"]["match"] for s in ss], ["fuzzy", "fuzzy"])
        self.assertEqual(ss[1]["dedupe"]["canonical_id"], "a_q001_o1")

    def test_opposites_not_clustered(self):
        ss = [stmt("a_q001_o1", "گویچه‌ی قرمز بالغ هسته دارد", True, exam="a"),
              stmt("b_q001_o1", "گویچه‌ی قرمز بالغ هسته ندارد", False, exam="b")]
        info = dedupe_statements(ss, 80)
        self.assertEqual(info["clusters"], 2)
        self.assertEqual([s["dedupe"]["occurrences"] for s in ss], [1, 1])
        self.assertFalse(any(s["review_reasons"] for s in ss))

    def test_truth_conflict_flags_pending_only(self):
        a = stmt("a_q001_o1", truth=True, year=1401, exam="a")
        b = stmt("b_q001_o1", truth=False, year=1402, exam="b")
        c = stmt("c_q001_o1", truth=None, year=1403, exam="c", reasons=["no_key"])
        dedupe_statements([a, b, c])
        self.assertIn(CONFLICT, a["review_reasons"])
        self.assertIn(CONFLICT, b["review_reasons"])
        self.assertTrue(a["needs_review"] and b["needs_review"])
        self.assertNotIn(CONFLICT, c["review_reasons"])        # truth نامعلوم تعارض نمی‌سازد
        # یک طرف تأییدشده ← فقط طرف pending پرچم می‌خورد
        a2, b2 = stmt("a_q001_o1", truth=True, year=1401, exam="a", status="approved"), stmt("b_q001_o1", truth=False, year=1402, exam="b")
        dedupe_statements([a2, b2])
        self.assertEqual((a2["review_reasons"], a2["needs_review"]), ([], False))
        self.assertIn(CONFLICT, b2["review_reasons"])
        # هر دو تأییدشده (تفاوت نسخه‌ی کتاب) ← هیچ پرچمی
        a3, b3 = [stmt(x, truth=t, year=y, exam=x[0], status="approved") for x, t, y in (("a_q001_o1", True, 1401), ("b_q001_o1", False, 1402))]
        dedupe_statements([a3, b3])
        self.assertFalse(a3["review_reasons"] or b3["review_reasons"])
        # رد‌شده تعارض نمی‌سازد
        a4, b4 = stmt("a_q001_o1", truth=True, exam="a"), stmt("b_q001_o1", truth=False, exam="b", status="rejected")
        dedupe_statements([a4, b4])
        self.assertFalse(a4["review_reasons"])

    def test_conflict_cleared_after_fix_and_idempotent(self):
        a, b = stmt("a_q001_o1", truth=True, exam="a"), stmt("b_q001_o1", truth=False, exam="b")
        dedupe_statements([a, b])
        dedupe_statements([a, b])
        self.assertEqual(b["review_reasons"], [CONFLICT])           # تکرار نمی‌شود
        b["truth"] = True
        dedupe_statements([a, b])
        self.assertEqual((a["review_reasons"], b["review_reasons"], b["needs_review"]), ([], [], False))

    def test_not_standalone_and_contextfree_items_excluded(self):
        fig1 = stmt("a_q001_o1", truth=True, exam="a", standalone=False)
        fig2 = stmt("b_q001_o1", truth=False, exam="b", standalone=False)
        dedupe_statements([fig1, fig2])
        self.assertFalse(fig1["review_reasons"] or fig2["review_reasons"])
        self.assertEqual(fig1["dedupe"]["occurrences"], 1)
        i1 = stmt("a_q001_i1", "مورد یک", True, exam="a", structure="combo_items")
        i2 = stmt("b_q001_i1", "مورد یک", False, exam="b", structure="combo_items")
        self.assertFalse(comparable(i1))
        dedupe_statements([i1, i2])
        self.assertEqual((i1["dedupe"]["occurrences"], i1["review_reasons"]), (1, []))
        # همان آیتم وقتی واقعاً مستقل شده باشد قابل‌مقایسه است
        j1 = stmt("a_q001_i1", "گویچه‌ی قرمز بالغ هسته دارد", True, exam="a", structure="combo_items", original="مورد یک")
        self.assertTrue(comparable(j1))

    def test_scales(self):
        # ۳۰۰۰ گزاره، هر متن دو بار (۱۵۰۰ خوشه‌ی دوتایی)، با اعداد متفاوت بین خوشه‌ها (نگهبان عدد نباید خوشه‌ها را قاطی کند)
        ss = [stmt(f"e{i}_q001_o1", f"جمله‌ی شماره‌ی {i % 1500} درباره‌ی موضوع آزمایشی است", year=1400 + i % 10, exam=f"e{i}")
              for i in range(3000)]
        t = time.time()
        info = dedupe_statements(ss)
        self.assertLess(time.time() - t, 20)
        self.assertEqual((info["clusters"], info["duplicated_statements"]), (1500, 3000))


class ProblemDedupe(unittest.TestCase):
    def test_problems(self):
        def prob(pid, stem, year):
            return {"problem_id": pid, "stem_original": stem, "review_status": "pending", "review_reasons": [],
                    "source": {"year_jalali": year, "exam_id": pid[0], "question_no": 1, "option_label": None}}
        ps = [prob("b_q001_prob", "در یک آمیزش فرضی ۴۰ فرزند ایجاد می‌شود", 1402),
              prob("a_q001_prob", "در یک آمیزش فرضی ۴۰ فرزند ایجاد می‌شود.", 1401),
              prob("c_q001_prob", "در یک آمیزش فرضی ۵۰ فرزند ایجاد می‌شود", 1403)]
        dedupe_problems(ps)
        self.assertEqual([p["dedupe"]["occurrences"] for p in ps], [2, 2, 1])
        self.assertEqual(ps[0]["dedupe"]["canonical_id"], "a_q001_prob")


if __name__ == "__main__":
    unittest.main()
