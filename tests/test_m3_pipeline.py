# -*- coding: utf-8 -*-
"""یکپارچه: extract → assemble → classify → atomize با Mock و PDF ساختگی واقعی."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from exambank.cache import DiskCache
from exambank.cli import build_parser, run_pipeline
from exambank.client import CachedClient, MockLLMClient
from exambank.stages.atomize import build_outputs
from exambank.state import StateStore
from exambank.utils import read_json, read_jsonl, write_json
from tests.test_extract_stage import make_pdf
from tests.test_taxonomy_classify import FAKE_TAX

O4 = lambda *t: [{"label": str(i), "text": x, "is_continuation": False} for i, x in enumerate(t, 1)]
ITEMS = " الف) مورد یک ب) مورد دو ج) مورد سه د) مورد چهار"
STEMS = {
    1: "کدام عبارت درباره‌ی موجود فرضی الف نادرست است؟",
    2: "کدام موارد درباره‌ی موجود فرضی ب درست‌اند؟" + ITEMS,
    3: "چند مورد از موارد زیر درباره‌ی موجود فرضی ج نادرست است؟" + ITEMS,
    4: "در یک آمیزش فرضی ۴۰ فرزند ایجاد می‌شود. چند فرزند همانند والد است؟",
    5: "با توجه به شکل، کدام عبارت درباره‌ی ساختار فرضی درست است؟",
}


def qq(n, opts):
    return {"number": n, "stem": STEMS[n], "options": O4(*opts), "continues_from_previous_page": False,
            "continues_on_next_page": False}


def ak(n, c, expl=None):
    return {"number": n, "correct": [c], "status": None, "explanation": expl,
            "continues_from_previous_page": False, "continues_on_next_page": False}


EXPL1 = "گزینه‌ی ۳ نادرست است چون عبارت سوم با توضیح فرضی مغایر است."
PAGES = {
    "PAGE-1": {"page_role": "questions", "answers": [], "figures": [], "questions": [
        qq(1, [f"عبارت {i}" for i in range(1, 5)]), qq(2, ["الف و ج", "فقط ب", "همه", "هیچ‌کدام"])]},
    "PAGE-2": {"page_role": "questions", "answers": [], "questions": [
        qq(3, ["۱", "۲", "۳", "۴"]), qq(4, ["۱۰", "۲۰", "۳۰", "۴۰"]),
        qq(5, [f"عبارت شکل {i}" for i in range(1, 5)])],
        "figures": [{"kind": "figure", "description_fa": "مستطیل سیاه", "labels_in_figure": [],
                     "box_2d": [300, 200, 700, 800], "question_numbers": [5]}]},
    "PAGE-3": {"page_role": "answer_key", "questions": [], "figures": [],
               "answers": [ak(1, "3"), ak(2, "1"), ak(3, "4"), ak(4, "2"), ak(5, "1")]},
    "PAGE-4": {"page_role": "explanations", "questions": [], "figures": [],
               "answers": [ak(1, "3", EXPL1)]},
}

CLS = {
    1: dict(question_type="textbook_recall", structure="single_best_statement", polarity="negative",
            polarity_evidence="نادرست است", route="statement", context_dependency="none"),
    2: dict(question_type="textbook_recall", structure="combo_items", polarity="positive",
            polarity_evidence="درست‌اند", route="statement", context_dependency="none"),
    3: dict(question_type="counting", structure="count_items", polarity="negative",
            polarity_evidence="نادرست است", route="statement", context_dependency="none"),
    4: dict(question_type="calculation", structure="calculation", polarity=None, polarity_evidence=None,
            route="problem", context_dependency="none"),
    5: dict(question_type="figure_based", structure="single_best_statement", polarity="positive",
            polarity_evidence="درست است", route="mixed", context_dependency="figure"),
}


def S(target, orig, text_standalone=None, **kw):
    d = {"target": target, "text_original": orig, "text_standalone": text_standalone or orig, "is_atomic": True,
         "standalone": True, "context_ref": None, "explicit_verdict": None, "why_false": None,
         "distractor_type": None, "parts": None}
    d.update(kw)
    return d


def which(parts):
    d = json.loads(parts[0][1])
    return next(n for n, s in STEMS.items() if d["stem"] == s), d


class Responder:
    def __init__(self):
        self.pages_seen = []

    def __call__(self, task, parts):
        if task == "extract_page":
            text = PdfReader(io.BytesIO(parts[0][1])).pages[0].extract_text()
            return json.loads(json.dumps(next(v for k, v in PAGES.items() if k in text)))
        n, d = which(parts)
        if task == "classify":
            c = dict(CLS[n], topics={"main": "tst-ch01-s01", "secondary": ["tst-ch02"] if n == 3 else []},
                     llm_confidence=0.9)
            return c
        if task == "problem":
            return {"problem_class": "genetics_cross", "givens": ["۴۰ فرزند ایجاد می‌شود"] if n == 4 else [],
                    "asked": "چند فرزند همانند والد است" if n == 4 else None,
                    "parametrization": {"is_candidate": n == 4, "slots": [
                        {"name": "تعداد فرزند", "value": "۴۰", "role": "given"}] if n == 4 else []}}
        assert task == "atomize", task
        if n == 1:
            st = [S(str(i), f"عبارت {i}") for i in range(1, 5)]
            st[2]["explicit_verdict"] = {"truth": False, "evidence": "گزینه‌ی ۳ نادرست است"}
            st[2]["why_false"] = "عبارت سوم با توضیح فرضی مغایر است"
            st[2]["distractor_type"] = "other"
            return {"statements": st, "option_semantics": []}
        if n in (2, 3):
            items = [S("الف", "مورد یک"), S("ب", "مورد دو"), S("ج", "مورد سه"), S("د", "مورد چهار")]
            return {"statements": items, "option_semantics": []}
        if n == 5:
            st = [S(str(i), f"عبارت شکل {i}", standalone=False, context_ref="exam-p2-f1") for i in range(1, 5)]
            return {"statements": st, "option_semantics": []}
        raise AssertionError(n)


class M3Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.pdf = self.d / "exam.pdf"
        make_pdf(self.pdf)
        self.tax = self.d / "tax.json"
        write_json(self.tax, FAKE_TAX)
        self.mock = MockLLMClient(Responder())
        self.factory = lambda cfg, cache: CachedClient(self.mock, cache, "m")
        self.logs = []
        self.proj = self.d / "proj"

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *extra, taxonomy=True):
        a = build_parser().parse_args(
            ["run-all", "-n", "proj", "-i", str(self.pdf), "-p", "1-4", "-o", str(self.d), "--year", "1403",
             "--group", "تجربی", "--exam-id", "exam", *(["--taxonomy", str(self.tax)] if taxonomy else []), *extra])
        return run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None)

    def calls(self, task):
        return sum(1 for c in self.mock.calls if c["task"] == task)

    def test_full_chain(self):
        self.assertEqual(self.run_it(), 0, "\n".join(self.logs))
        self.assertEqual({t: self.calls(t) for t in ("extract_page", "classify", "atomize", "problem")},
                         {"extract_page": 4, "classify": 5, "atomize": 4, "problem": 2})
        stmts = read_jsonl(self.proj / "statements_raw.jsonl")
        probs = read_jsonl(self.proj / "problems_raw.jsonl")
        self.assertEqual((len(stmts), len(probs)), (16, 2))
        by_q = {}
        for s in stmts:
            by_q.setdefault(s["question_id"], []).append(s)
        # Q1: منفی، کلید ۳
        q1 = by_q["exam_q001"]
        self.assertEqual([s["truth"] for s in q1], [True, True, False, True])
        self.assertEqual(q1[2]["truth_basis"], "explanation")          # حکم صریح تأییدشده
        self.assertEqual(q1[2]["why_false"], "عبارت سوم با توضیح فرضی مغایر است")
        self.assertEqual(q1[0]["truth_basis"], "key_derived")
        # Q2: combo «الف و ج»
        self.assertEqual([s["truth"] for s in by_q["exam_q002"]], [True, False, True, False])
        # Q3: چند مورد نادرست، کلید ۴ = n ← همه غلط
        self.assertEqual([s["truth"] for s in by_q["exam_q003"]], [False] * 4)
        self.assertEqual(by_q["exam_q003"][0]["topic_ids"], ["tst-ch01-s01", "tst-ch02"])
        # Q5: mixed ← گزاره‌های وابسته به شکل + مسئله
        q5 = by_q["exam_q005"]
        self.assertTrue(all(not s["standalone"] and s["context_ref"] == "exam-p2-f1" and
                            s["context_dependency"] == "figure" for s in q5))
        self.assertNotIn("exam_q004", by_q)  # route=problem فقط در بانک مسئله
        self.assertEqual(sorted(p["question_id"] for p in probs), ["exam_q004", "exam_q005"])
        p4 = next(p for p in probs if p["question_id"] == "exam_q004")
        self.assertEqual((p4["correct"], p4["problem_class"], p4["figure_refs"]), (["2"], "genetics_cross", []))
        self.assertTrue(p4["parametrization"]["is_candidate"])
        self.assertEqual(next(p for p in probs if p["question_id"] == "exam_q005")["figure_refs"], ["exam-p2-f1"])
        # قرارداد: هیچ truth بدون basis و هیچ رکورد بدون source کامل
        for s in stmts:
            self.assertEqual(s["truth"] is None, s["truth_basis"] == "unresolved")
            self.assertTrue(all(s["source"].get(k) is not None for k in ("exam_id", "year_jalali", "group", "question_no", "option_label", "pages")))
        rep = read_json(self.proj / "atomize_report.json")
        self.assertEqual((rep["statements"], rep["problems"], rep["missing_atomize"], rep["missing_problem"]), (16, 2, [], []))

    def test_second_run_zero_llm_calls(self):
        self.run_it()
        n = len(self.mock.calls)
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(len(self.mock.calls), n)
        self.assertEqual(self.run_it("--restart"), 0)          # state پاک، کش جواب می‌دهد
        self.assertEqual(len(self.mock.calls), n)

    def test_missing_taxonomy_stops_classify_with_clear_message(self):
        self.tax.unlink()
        self.assertEqual(self.run_it(), 1)
        self.assertEqual(self.calls("classify"), 0)
        self.assertTrue(any("build-taxonomy" in m for m in self.logs))
        self.assertTrue((self.proj / "assembled.jsonl").exists())   # مراحل قبلی انجام شده‌اند

    def test_classify_failure_for_one_question_does_not_lose_others(self):
        from exambank.client import LLMInvalidResponse
        base = Responder()

        def flaky(task, parts):
            if task == "classify" and which(parts)[0] == 2:
                return {"structure": "bogus"}   # نامعتبر
            return base(task, parts)
        self.mock = MockLLMClient(flaky)
        self.assertEqual(self.run_it(), 1)
        self.assertEqual(len(read_jsonl(self.proj / "classified.jsonl")), 4)
        self.assertEqual(StateStore(self.proj / "state.json").failed("classify"), ["exam_q002"])
        # پس از رفع، فقط همان سوال دوباره اجرا می‌شود
        self.mock = MockLLMClient(base)
        before = 0
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.calls("classify"), 1)

    def test_rules_change_needs_no_llm(self):
        """build_outputs از work/ ساخته می‌شود؛ اجرای دوباره‌ی آن هیچ فراخوانی ندارد و خروجی یکسان است."""
        self.run_it()
        before = (self.proj / "statements_raw.jsonl").read_text(encoding="utf-8")
        n = len(self.mock.calls)
        build_outputs(self.proj)
        self.assertEqual((self.proj / "statements_raw.jsonl").read_text(encoding="utf-8"), before)
        self.assertEqual(len(self.mock.calls), n)

    def test_hallucinated_explicit_verdict_cannot_change_truth(self):
        base = Responder()

        def liar(task, parts):
            r = base(task, parts)
            if task == "atomize" and which(parts)[0] == 1:
                r["statements"][0]["explicit_verdict"] = {"truth": False, "evidence": "عبارتی که در پاسخ نیست"}
            return r
        self.mock = MockLLMClient(liar)
        self.run_it()
        q1 = [s for s in read_jsonl(self.proj / "statements_raw.jsonl") if s["question_id"] == "exam_q001"]
        self.assertIs(q1[0]["truth"], True)
        self.assertEqual(q1[0]["truth_basis"], "key_derived")
        self.assertIn("evidence_not_found", q1[0]["review_reasons"])

    def test_quota_during_atomize_stops_and_resumes(self):
        from exambank.client import LLMQuotaExhausted
        base = Responder()

        def quota(task, parts):
            if task == "atomize" and which(parts)[0] == 3:
                return LLMQuotaExhausted("RequestsPerDay")
            return base(task, parts)
        self.mock = MockLLMClient(quota)
        self.assertEqual(self.run_it(), 1)
        self.assertIsNotNone(StateStore(self.proj / "state.json").quota)
        self.mock = MockLLMClient(base)
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.calls("extract_page") + self.calls("classify"), 0)  # فقط ادامه‌ی atomize
        self.assertEqual(len(read_jsonl(self.proj / "statements_raw.jsonl")), 16)


if __name__ == "__main__":
    unittest.main()
