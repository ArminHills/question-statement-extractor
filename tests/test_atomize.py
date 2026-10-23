# -*- coding: utf-8 -*-
import unittest

from exambank.stages.atomize import (build_problem, build_statements, validate_atomize_payload,
                                     validate_problem_payload)

STEM_SINGLE = "کدام عبارت درباره‌ی موجود فرضی نادرست است؟"
STEM_ITEMS = "کدام موارد درباره‌ی موجود فرضی درست‌اند؟ الف) مورد یک ب) مورد دو ج) مورد سه د) مورد چهار"
EXPL = "گزینه‌ی ۳ نادرست است چون عبارت سوم با توضیح فرضی مغایر است. گزینه‌ی ۱ درست است."


def mkq(structure="single_best_statement", polarity="negative", correct=("3",), status="official",
        stem=STEM_SINGLE, options=None, explanation=None, **kw):
    options = options or [f"عبارت {i}" for i in range(1, 5)]
    q = {
        "question_id": "ex-1403_q001", "exam": {"exam_id": "ex-1403", "year_jalali": 1403, "group": "g"},
        "number": 1, "stem_original": stem, "stem": stem,
        "options": [{"label": str(i), "text_original": t, "text": t} for i, t in enumerate(options, 1)],
        "correct": list(correct), "answer_status": status, "explanation_original": explanation,
        "structure": structure, "polarity": polarity, "question_type": "textbook_recall",
        "topic_ids": ["tst-ch01-s01"], "context_dependency": "none", "figure_ids": [],
        "pages": {"question": [1], "answer": [9]}, "review_reasons": [], "llm_confidence": 0.8, "figures": []}
    q.update(kw)
    return q


def S(target, orig, text_standalone=None, **kw):
    d = {"target": target, "text_original": orig, "text_standalone": text_standalone if text_standalone is not None else orig,
         "is_atomic": True, "standalone": True, "context_ref": None, "explicit_verdict": None, "why_false": None,
         "distractor_type": None, "parts": None}
    d.update(kw)
    return d


def payload(stmts, sems=()):
    return validate_atomize_payload({"statements": list(stmts), "option_semantics": list(sems)})


def single_payload(**override):
    return payload([S(str(i), f"عبارت {i}", **override.get(str(i), {})) for i in range(1, 5)])


def item_stmts():
    return [S("الف", "مورد یک"), S("ب", "مورد دو"), S("ج", "مورد سه"), S("د", "مورد چهار")]


def truths(stmts):
    return [s["truth"] for s in stmts]


def invariants(tc, stmts):
    for s in stmts:
        if s["truth"] is not None:
            tc.assertNotEqual(s["truth_basis"], "unresolved", s["statement_id"])
        else:
            tc.assertEqual(s["truth_basis"], "unresolved", s["statement_id"])
            tc.assertTrue(s["needs_review"], f"null truth بدون پرچم: {s['statement_id']}")
        tc.assertEqual(s["needs_review"], bool(s["review_reasons"]))
        src = s["source"]
        for k in ("exam_id", "year_jalali", "group", "question_no", "option_label", "pages"):
            tc.assertIn(k, src)
            tc.assertIsNotNone(src[k], k)
        tc.assertTrue(s["text_original"].strip() and s["text_standalone"].strip())
        tc.assertEqual(s["review_status"], "pending")


class SingleStatement(unittest.TestCase):
    def test_negative_from_key(self):
        st, _ = build_statements(mkq(), single_payload())
        self.assertEqual(truths(st), [True, True, False, True])
        self.assertEqual([s["statement_id"] for s in st], [f"ex-1403_q001_o{i}" for i in range(1, 5)])
        self.assertTrue(all(s["truth_basis"] == "key_derived" and not s["needs_review"] for s in st))
        self.assertEqual(st[2]["source"]["option_label"], "3")
        self.assertEqual(st[0]["polarity"], "negative")
        invariants(self, st)

    def test_text_original_comes_from_source_not_llm(self):
        p = payload([S(str(i), "متن ساختگی LLM") for i in range(1, 5)])
        st, _ = build_statements(mkq(), p)
        self.assertEqual([s["text_original"] for s in st], [f"عبارت {i}" for i in range(1, 5)])

    def test_completion_positive_standalone(self):
        q = mkq("completion", "positive", ["4"], stem="کدام یاخته فاقد هسته است؟",
                options=["یاخته‌ی اول", "یاخته‌ی دوم", "یاخته‌ی سوم", "گویچه‌ی قرمز بالغ"])
        p = payload([S(str(i + 1), o["text_original"], f"{o['text_original']} فاقد هسته است")
                     for i, o in enumerate(q["options"])])
        st, _ = build_statements(q, p)
        self.assertEqual(truths(st), [False, False, False, True])
        self.assertEqual(st[3]["text_standalone"], "گویچه‌ی قرمز بالغ فاقد هسته است")
        self.assertFalse(any("standalone_adds_words" in s["review_reasons"] for s in st))
        invariants(self, st)

    def test_adding_facts_is_flagged(self):
        q = mkq()
        p = single_payload(**{"2": {"text_standalone": "عبارت دوم به دلیل وجود میتوکندری‌های فراوان هیچ‌گاه رخ نمی‌دهد"}})
        st, _ = build_statements(q, p)
        self.assertIn("standalone_adds_words", st[1]["review_reasons"])
        self.assertNotIn("standalone_adds_words", st[0]["review_reasons"])

    def test_false_statement_not_corrected_by_code(self):
        st, _ = build_statements(mkq(), single_payload())
        self.assertEqual(st[2]["text_standalone"], "عبارت 3")

    def test_annulled_all_null_flagged(self):
        st, _ = build_statements(mkq(status="annulled", correct=[]), single_payload())
        self.assertEqual(truths(st), [None] * 4)
        self.assertTrue(all("answer_status_annulled" in s["review_reasons"] for s in st))
        invariants(self, st)

    def test_two_keys(self):
        st, _ = build_statements(mkq("completion", "positive", ["1", "3"], status="multiple_keys"), single_payload())
        self.assertEqual(truths(st), [True, False, True, False])

    def test_missing_llm_statement(self):
        p = payload([S("1", "x"), S("2", "x"), S("3", "x")])
        st, _ = build_statements(mkq(), p)
        self.assertEqual(len(st), 4)
        self.assertIn("llm_statement_missing", st[3]["review_reasons"])
        self.assertEqual(st[3]["text_standalone"], "عبارت 4")
        self.assertIn("standalone_missing", st[3]["review_reasons"])

    def test_question_level_reasons_propagate(self):
        st, _ = build_statements(mkq(review_reasons=["polarity_disagreement"]), single_payload())
        self.assertTrue(all("polarity_disagreement" in s["review_reasons"] and s["needs_review"] for s in st))

    def test_polarity_missing_all_null(self):
        st, _ = build_statements(mkq(polarity=None), single_payload())
        self.assertEqual(truths(st), [None] * 4)


class ExplicitVerdicts(unittest.TestCase):
    def verdict(self, truth, evidence):
        return {"explicit_verdict": {"truth": truth, "evidence": evidence}}

    def test_verified_evidence_sets_basis_explanation(self):
        p = single_payload(**{"3": self.verdict(False, "گزینه‌ی ۳ نادرست است")})
        st, _ = build_statements(mkq(explanation=EXPL), p)
        self.assertEqual(st[2]["truth_basis"], "explanation")
        self.assertEqual(st[2]["truth_evidence"], "گزینه‌ی ۳ نادرست است")
        self.assertFalse(st[2]["needs_review"])
        self.assertEqual(st[0]["truth_basis"], "key_derived")

    def test_invented_evidence_ignored_and_flagged(self):
        p = single_payload(**{"2": self.verdict(True, "گزینه‌ی ۲ کاملاً درست است")})
        st, _ = build_statements(mkq(explanation=EXPL), p)
        self.assertEqual(st[1]["truth_basis"], "key_derived")   # حکم ساختگی اثری ندارد
        self.assertTrue(st[1]["truth"])                         # از کلید: منفی و کلید=۳ ← گزینه‌ی ۲ صحیح
        self.assertIn("evidence_not_found", st[1]["review_reasons"])

    def test_verdict_without_any_explanation(self):
        p = single_payload(**{"2": self.verdict(True, "هر چیزی")})
        st, _ = build_statements(mkq(explanation=None), p)
        self.assertIn("evidence_not_found", st[1]["review_reasons"])

    def test_conflict_with_key(self):
        p = single_payload(**{"1": self.verdict(False, "گزینه‌ی ۱ درست است")})  # نقل واقعی ولی truth وارونه‌ی ادعایی
        st, _ = build_statements(mkq(explanation=EXPL), p)
        self.assertIs(st[0]["truth"], False)
        self.assertIn("conflict_explanation_vs_key", st[0]["review_reasons"])

    def test_why_false_verbatim_only(self):
        ok = single_payload(**{"3": {"why_false": "عبارت سوم با توضیح فرضی مغایر است", "distractor_type": "other"}})
        st, _ = build_statements(mkq(explanation=EXPL), ok)
        self.assertEqual(st[2]["why_false"], "عبارت سوم با توضیح فرضی مغایر است")
        self.assertEqual(st[2]["distractor_type"], "other")
        bad = single_payload(**{"3": {"why_false": "چون یک دلیل ساختگی دارد", "distractor_type": "other"}})
        st, _ = build_statements(mkq(explanation=EXPL), bad)
        self.assertIsNone(st[2]["why_false"])
        self.assertIsNone(st[2]["distractor_type"])
        self.assertIn("why_false_not_found", st[2]["review_reasons"])

    def test_why_false_dropped_for_true_statements(self):
        p = single_payload(**{"1": {"why_false": "گزینه‌ی ۱ درست است"}})
        st, _ = build_statements(mkq(explanation=EXPL), p)
        self.assertIsNone(st[0]["why_false"])

    def test_parts_accepted_only_with_verified_verdicts(self):
        parts = [{"text_original": "بخش یک", "text_standalone": "بخش یک درست است",
                  "explicit_verdict": {"truth": True, "evidence": "گزینه‌ی ۱ درست است"}},
                 {"text_original": "بخش دو", "text_standalone": "بخش دو نادرست است",
                  "explicit_verdict": {"truth": False, "evidence": "گزینه‌ی ۳ نادرست است"}}]
        q = mkq(options=["بخش یک و بخش دو", "ب", "ج", "د"], explanation=EXPL, correct=["2"])
        p = payload([S("1", "x", is_atomic=False, parts=parts)] + [S(str(i), "x") for i in (2, 3, 4)])
        st, _ = build_statements(q, p)
        kids = [s for s in st if s["parent_statement_id"]]
        self.assertEqual([k["statement_id"] for k in kids], ["ex-1403_q001_o1_p1", "ex-1403_q001_o1_p2"])
        self.assertEqual([k["truth"] for k in kids], [True, False])
        self.assertTrue(all(k["truth_basis"] == "explanation" and k["is_atomic"] for k in kids))
        self.assertFalse(st[0]["is_atomic"])
        invariants(self, st)
        # شاهد ساختگی ← تجزیه رد می‌شود
        parts[1]["explicit_verdict"]["evidence"] = "جمله‌ای که نیست"
        p = payload([S("1", "x", is_atomic=False, parts=parts)] + [S(str(i), "x") for i in (2, 3, 4)])
        st, _ = build_statements(q, p)
        self.assertFalse([s for s in st if s["parent_statement_id"]])
        self.assertIn("parts_rejected", st[0]["review_reasons"])
        self.assertFalse(st[0]["is_atomic"])  # مرکب می‌ماند ولی تجزیه نمی‌شود

    def test_composite_without_parts_stays_whole(self):
        p = single_payload(**{"1": {"is_atomic": False}})
        st, _ = build_statements(mkq(), p)
        self.assertEqual(len(st), 4)
        self.assertFalse(st[0]["is_atomic"])


class Context(unittest.TestCase):
    def test_figure_dependent(self):
        q = mkq(context_dependency="figure", figure_ids=["ex-p2-f1"])
        p = single_payload(**{"1": {"standalone": False, "context_ref": "ex-p2-f1"}})
        st, _ = build_statements(q, p)
        self.assertFalse(st[0]["standalone"])
        self.assertEqual((st[0]["context_dependency"], st[0]["context_ref"]), ("figure", "ex-p2-f1"))
        self.assertTrue(st[1]["standalone"])
        self.assertEqual(st[1]["context_dependency"], "none")
        self.assertNotIn("context_ref_invalid", st[0]["review_reasons"])

    def test_invalid_ref_flagged(self):
        q = mkq(context_dependency="figure", figure_ids=["ex-p2-f1"])
        p = single_payload(**{"1": {"standalone": False, "context_ref": "invented-fig"}})
        st, _ = build_statements(q, p)
        self.assertIn("context_ref_invalid", st[0]["review_reasons"])
        self.assertIsNone(st[0]["context_ref"])

    def test_shared_scenario_and_inconsistency(self):
        q = mkq(context_dependency="shared_scenario")
        p = single_payload(**{"1": {"standalone": False, "context_ref": "shared_scenario"}})
        st, _ = build_statements(q, p)
        self.assertEqual(st[0]["context_ref"], "shared_scenario")
        st, _ = build_statements(mkq(), p)  # سوال می‌گوید none ولی گزاره وابسته است
        self.assertIn("context_inconsistent", st[0]["review_reasons"])


class ComboItems(unittest.TestCase):
    OPTS = ["الف و ج", "فقط ب", "همه", "هیچ‌کدام"]

    def q(self, polarity="positive", key="1", correct=None, **kw):
        return mkq("combo_items", polarity, correct or [key], stem=STEM_ITEMS, options=self.OPTS, **kw)

    def test_set(self):
        st, _ = build_statements(self.q(), payload(item_stmts()))
        self.assertEqual(truths(st), [True, False, True, False])
        self.assertEqual([s["statement_id"] for s in st], [f"ex-1403_q001_i{i}" for i in range(1, 5)])
        self.assertEqual([s["source"]["option_label"] for s in st], ["الف", "ب", "ج", "د"])
        invariants(self, st)

    def test_negative_set(self):
        st, _ = build_statements(self.q("negative"), payload(item_stmts()))
        self.assertEqual(truths(st), [False, True, False, True])

    def test_all_none_only(self):
        self.assertEqual(truths(build_statements(self.q(key="3"), payload(item_stmts()))[0]), [True] * 4)
        self.assertEqual(truths(build_statements(self.q(key="4"), payload(item_stmts()))[0]), [False] * 4)
        self.assertEqual(truths(build_statements(self.q(key="2"), payload(item_stmts()))[0]), [False, True, False, False])

    def test_two_keys_null(self):
        st, _ = build_statements(self.q(status="multiple_keys", correct=["1", "2"]), payload(item_stmts()))
        self.assertEqual(truths(st), [None] * 4)

    def test_item_text_must_be_in_stem(self):
        items = item_stmts()
        items[1] = S("ب", "متنی که در صورت سوال نیست")
        st, _ = build_statements(self.q(), payload(items))
        self.assertIn("item_text_not_in_stem", st[1]["review_reasons"])
        self.assertNotIn("item_text_not_in_stem", st[0]["review_reasons"])

    def test_llm_disagreement_uses_parser_and_flags(self):
        sems = [{"label": "1", "kind": "set", "items": ["الف", "ب"], "count": None}]
        st, _ = build_statements(self.q(), payload(item_stmts(), sems))
        self.assertEqual(truths(st), [True, False, True, False])  # تحلیل‌گر قاعده‌محور برنده است
        self.assertTrue(all("option_parse_disagreement" in s["review_reasons"] for s in st))

    def test_disagreement_on_non_key_option_is_irrelevant(self):
        sems = [{"label": "2", "kind": "set", "items": ["ج"], "count": None}]
        st, _ = build_statements(self.q(), payload(item_stmts(), sems))
        self.assertFalse(any("option_parse_disagreement" in s["review_reasons"] for s in st))

    def test_llm_only_semantics_flagged(self):
        q = mkq("combo_items", "positive", ["1"], stem=STEM_ITEMS, options=["مورد نخست و سوم", "ب", "ج", "د"])
        sems = [{"label": "1", "kind": "set", "items": ["الف", "ج"], "count": None}]
        st, _ = build_statements(q, payload(item_stmts(), sems))
        self.assertEqual(truths(st), [True, False, True, False])
        self.assertTrue(all("option_semantics_llm_only" in s["review_reasons"] for s in st))
        # بدون هیچ معنایی ← null
        st, _ = build_statements(q, payload(item_stmts()))
        self.assertEqual(truths(st), [None] * 4)

    def test_matching_unparseable_is_null(self):
        q = mkq("matching", "positive", ["1"], stem=STEM_ITEMS, options=["ترکیب ۱", "ب", "ج", "د"])
        st, _ = build_statements(q, payload(item_stmts()))
        self.assertEqual(truths(st), [None] * 4)
        self.assertIn("matching_ambiguous", st[0]["review_reasons"])

    def test_fewer_than_two_items(self):
        st, reasons = build_statements(self.q(), payload([S("الف", "مورد یک")]))
        self.assertEqual(st, [])
        self.assertIn("items_missing", reasons)


class CountItems(unittest.TestCase):
    STEM = "چند مورد درباره‌ی موجود فرضی نادرست است؟ الف) مورد یک ب) مورد دو ج) مورد سه د) مورد چهار"
    OPTS = ["۱", "۲", "۳", "۴"]

    def q(self, polarity, key, **kw):
        return mkq("count_items", polarity, [key], stem=self.STEM, options=self.OPTS, **kw)

    def test_c_equals_n_negative(self):
        st, _ = build_statements(self.q("negative", "4"), payload(item_stmts()))
        self.assertEqual(truths(st), [False] * 4)
        invariants(self, st)

    def test_c_zero_not_offered_but_n_positive(self):
        st, _ = build_statements(self.q("positive", "4"), payload(item_stmts()))
        self.assertEqual(truths(st), [True] * 4)

    def test_middle_is_null_and_flagged(self):
        st, _ = build_statements(self.q("positive", "2"), payload(item_stmts()))
        self.assertEqual(truths(st), [None] * 4)
        self.assertIn("count_partial_unresolved", st[0]["review_reasons"])
        invariants(self, st)

    def test_middle_resolved_by_explanation(self):
        items = item_stmts()
        for i, ev in ((0, "مورد یک درست است"), (1, "مورد دو درست است")):
            items[i]["explicit_verdict"] = {"truth": True, "evidence": ev}
        q = self.q("positive", "2", explanation="مورد یک درست است. مورد دو درست است.")
        st, _ = build_statements(q, payload(items))
        self.assertEqual(truths(st), [True, True, False, False])
        self.assertEqual([s["truth_basis"] for s in st], ["explanation", "explanation", "key_derived", "key_derived"])
        self.assertFalse(any(s["needs_review"] for s in st))

    def test_count_from_llm_only_flagged(self):
        q = mkq("count_items", "positive", ["1"], stem=self.STEM, options=["همین‌قدر مبهم", "۲", "۳", "۴"])
        sems = [{"label": "1", "kind": "count", "items": [], "count": 4}]
        st, _ = build_statements(q, payload(item_stmts(), sems))
        self.assertEqual(truths(st), [True] * 4)
        self.assertIn("option_semantics_llm_only", st[0]["review_reasons"])


class PayloadValidation(unittest.TestCase):
    def test_atomize_rejects(self):
        bads = [{}, {"statements": []}, {"statements": "x"},
                {"statements": [{"target": "1", "text_original": "a"}]},
                {"statements": [S("1", "a"), S("1", "b")]},
                {"statements": [S("1", "a", explicit_verdict={"truth": "yes", "evidence": "x"})]},
                {"statements": [S("1", "a", explicit_verdict={"truth": True, "evidence": " "})]},
                {"statements": [S("1", "a", parts=[{"text_original": "p", "text_standalone": "p"}])]},
                {"statements": [S("1", "a")], "option_semantics": [{"label": "1", "kind": "bogus"}]},
                {"statements": [S("1", "a")], "option_semantics": [{"label": "1", "kind": "count", "count": "2"}]}]
        for b in bads:
            with self.assertRaises(ValueError, msg=str(b)):
                validate_atomize_payload(b)

    def test_problem_validator(self):
        ok = validate_problem_payload({"problem_class": "genetics_cross", "givens": ["۴۰ فرزند"], "asked": "چند",
                                       "parametrization": {"is_candidate": True, "slots": [
                                           {"name": "n", "value": "۴۰", "role": "given"}]}})
        self.assertEqual(ok["problem_class"], "genetics_cross")
        for b in ({"problem_class": "Bad Class", "givens": []}, {"problem_class": "x", "givens": [""]},
                  {"problem_class": "x", "givens": [], "parametrization": {"is_candidate": "yes", "slots": []}},
                  {"problem_class": "x", "givens": [], "parametrization": {"is_candidate": True, "slots": [
                      {"name": "n", "value": "1", "role": "weird"}]}}, []):
            with self.assertRaises(ValueError, msg=str(b)):
                validate_problem_payload(b)


class Problems(unittest.TestCase):
    STEM = "در یک آمیزش فرضی ۴۰ فرزند ایجاد می‌شود. چند فرزند همانند والد است؟"

    def q(self, **kw):
        base = mkq("calculation", None, ["2"], stem=self.STEM, options=["۱۰", "۲۰", "۳۰", "۴۰"],
                   explanation="پاسخ ۲۰ است زیرا ...", route="problem", question_type="calculation")
        base.update(kw)
        return base

    def llm(self, **kw):
        d = {"problem_class": "genetics_cross", "givens": ["۴۰ فرزند ایجاد می‌شود"], "asked": "چند فرزند همانند والد است",
             "parametrization": {"is_candidate": True, "slots": [{"name": "تعداد", "value": "۴۰", "role": "given"}]}}
        d.update(kw)
        return validate_problem_payload(d)

    def test_clean(self):
        p = build_problem(self.q(), self.llm())
        self.assertEqual((p["problem_id"], p["correct"], p["solution_text"]), ("ex-1403_q001_prob", ["2"], "پاسخ ۲۰ است زیرا ..."))
        self.assertFalse(p["needs_review"])
        self.assertTrue(p["parametrization"]["is_candidate"])
        self.assertEqual(p["source"]["question_no"], 1)

    def test_invented_given_and_slot(self):
        p = build_problem(self.q(), self.llm(givens=["عددی که نیست"], asked="چیزی که خواسته نشده",
                                             parametrization={"is_candidate": True, "slots": [
                                                 {"name": "n", "value": "۹۹۹", "role": "given"}]}))
        for r in ("given_not_in_stem", "asked_not_in_stem", "slot_value_not_in_stem", "parametrization_dropped"):
            self.assertIn(r, p["review_reasons"])
        self.assertFalse(p["parametrization"]["is_candidate"])
        self.assertEqual(p["parametrization"]["slots"], [])

    def test_new_class_flagged(self):
        p = build_problem(self.q(), self.llm(problem_class="enzyme_kinetics"))
        self.assertIn("new_problem_class", p["review_reasons"])

    def test_no_solution_is_none_not_invented(self):
        p = build_problem(self.q(explanation_original=None), self.llm())
        self.assertIsNone(p["solution_text"])

    def test_annulled_problem_keeps_status(self):
        p = build_problem(self.q(answer_status="annulled", correct=[]), self.llm())
        self.assertEqual((p["answer_status"], p["correct"]), ("annulled", []))


if __name__ == "__main__":
    unittest.main()
