# -*- coding: utf-8 -*-
import copy
import json
import tempfile
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from exambank.client import MockLLMClient
from exambank.runner import run_items
from exambank.stages.classify import (apply_classification, classification_reasons, validate_classify_payload)
from exambank.stages.taxonomy_build import run_build_taxonomy, validate_toc_payload
from exambank.state import StateStore
from exambank.taxonomy import (TaxonomyInvalid, TaxonomyMissing, describe_for_prompt, load_taxonomy,
                               merge_toc_pages, save_book, topic_ids, validate_taxonomy)
from exambank.utils import read_json, write_json

# داده‌ی آزمایشی ساختگی؛ هیچ ارتباطی با فصل‌های واقعی کتاب ندارد.
FAKE_BOOK = {"book_id": "tst", "title": "کتاب آزمایشی", "grade": 10, "source": "fake", "chapters": [
    {"chapter_id": "tst-ch01", "number": 1, "title": "فصل آزمایشی ۱", "sections": [
        {"section_id": "tst-ch01-s01", "number": 1, "title": "گفتار الف"},
        {"section_id": "tst-ch01-s02", "number": 2, "title": "گفتار ب"}]},
    {"chapter_id": "tst-ch02", "number": 2, "title": "فصل بدون گفتار", "sections": []}]}
FAKE_TAX = {"version": 1, "books": [FAKE_BOOK]}
TOPICS = set(topic_ids(FAKE_TAX))


def good_cls(**kw):
    d = {"question_type": "textbook_recall", "structure": "single_best_statement", "polarity": "negative",
         "polarity_evidence": "نادرست است", "topics": {"main": "tst-ch01-s01", "secondary": []},
         "route": "statement", "context_dependency": "none", "llm_confidence": 0.9}
    d.update(kw)
    return d


class TaxonomyTests(unittest.TestCase):
    def test_missing_file_message_tells_what_is_needed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TaxonomyMissing) as cm:
                load_taxonomy(Path(d) / "nope.json")
            self.assertIn("build-taxonomy", str(cm.exception))

    def test_topic_ids_and_prompt(self):
        self.assertEqual(topic_ids(FAKE_TAX), ["tst-ch01-s01", "tst-ch01-s02", "tst-ch02"])
        text = describe_for_prompt(FAKE_TAX)
        self.assertIn("tst-ch01-s02 |", text)
        self.assertIn("tst-ch02 |", text)

    def test_validation(self):
        bad = copy.deepcopy(FAKE_TAX)
        bad["books"][0]["chapters"][1]["chapter_id"] = "tst-ch01"
        with self.assertRaises(TaxonomyInvalid):
            validate_taxonomy(bad)
        with self.assertRaises(TaxonomyInvalid):
            validate_taxonomy({"books": []})

    def test_corrupt_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text("{bad", encoding="utf-8")
            with self.assertRaises(TaxonomyInvalid):
                load_taxonomy(p)

    def test_merge_pages_continuation_and_stable_ids(self):
        p1 = [{"chapter_number": 1, "chapter_title": "ك اول", "sections": [{"number": 1, "title": "الف"}]}]
        p2 = [{"chapter_number": 1, "chapter_title": "", "sections": [{"number": 2, "title": "ب"}]},
              {"chapter_number": 2, "chapter_title": "دوم", "sections": []}]
        b = merge_toc_pages("x10", "زیست ي", 10, [p1, p2])
        self.assertEqual([c["chapter_id"] for c in b["chapters"]], ["x10-ch01", "x10-ch02"])
        self.assertEqual([s["section_id"] for s in b["chapters"][0]["sections"]], ["x10-ch01-s01", "x10-ch01-s02"])
        self.assertEqual(b["chapters"][0]["title"], "ک اول")      # نرمال‌شده
        self.assertEqual(b["title"], "زیست ی")

    def test_save_book_versions_and_force(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tax.json"
            self.assertEqual(save_book(path, FAKE_BOOK)["version"], 1)
            with self.assertRaises(TaxonomyInvalid):
                save_book(path, FAKE_BOOK)
            self.assertEqual(save_book(path, FAKE_BOOK, force=True)["version"], 2)
            other = dict(FAKE_BOOK, book_id="tst2", chapters=[
                {"chapter_id": "tst2-ch01", "number": 1, "title": "t", "sections": []}])
            tax = save_book(path, other)
            self.assertEqual([b["book_id"] for b in tax["books"]], ["tst", "tst2"])
            self.assertEqual(load_taxonomy(path)["version"], 3)

    def test_toc_validator(self):
        ok = validate_toc_payload([{"chapter_number": "۱", "chapter_title": " ت ", "sections": [{"number": 1, "title": "x"}]}])
        self.assertEqual(ok[0]["chapter_number"], 1)
        self.assertEqual(validate_toc_payload([]), [])
        for bad in ({"a": 1}, [{"chapter_title": "x", "sections": []}],
                    [{"chapter_number": 1, "chapter_title": "x", "sections": [{"number": 1, "title": ""}]}]):
            with self.assertRaises(ValueError):
                validate_toc_payload(bad)


def make_toc_pdf(path, n=2):
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(1, n + 1):
        c.drawString(100, 780, f"TOC-{i}")
        c.showPage()
    c.save()


class BuildTaxonomyStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.pdf = self.d / "toc.pdf"
        make_toc_pdf(self.pdf)
        self.state = StateStore(self.d / "state.json")
        self.tax_path = self.d / "tax.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, mock, **kw):
        return run_build_taxonomy(pdf_path=self.pdf, pages=[1, 2], project_dir=self.d, client=mock, model="m",
                                  state=self.state, book_id="x10", title="کتاب", grade=10,
                                  taxonomy_path=self.tax_path, log=lambda m: None, delay=0, sleep=lambda s: None, **kw)

    def test_builds_from_pages_only(self):
        pages = {"TOC-1": [{"chapter_number": 1, "chapter_title": "الف", "sections": [{"number": 1, "title": "ب"}]}],
                 "TOC-2": [{"chapter_number": 2, "chapter_title": "ج", "sections": [{"number": 1, "title": "د"}]}]}

        def resp(task, parts):
            import io
            from pypdf import PdfReader
            text = PdfReader(io.BytesIO(parts[0][1])).pages[0].extract_text()
            return next(v for k, v in pages.items() if k in text)
        mock = MockLLMClient(resp)
        summary, tax = self.run_it(mock)
        self.assertTrue(summary.ok)
        self.assertEqual(topic_ids(load_taxonomy(self.tax_path)), ["x10-ch01-s01", "x10-ch02-s01"])
        self.assertEqual(len(mock.calls), 2)

    def test_incomplete_run_writes_nothing(self):
        from exambank.client import LLMInvalidResponse
        seq = iter([[{"chapter_number": 1, "chapter_title": "الف", "sections": []}], LLMInvalidResponse("خالی")])
        mock = MockLLMClient(lambda t, p: next(seq))
        summary, tax = self.run_it(mock)
        self.assertFalse(summary.ok)
        self.assertIsNone(tax)
        self.assertFalse(self.tax_path.exists())


class ClassifyValidation(unittest.TestCase):
    def test_good(self):
        r = validate_classify_payload(good_cls(topics={"main": "tst-ch01-s01", "secondary": ["tst-ch02"]}), TOPICS)
        self.assertEqual(r["topic_ids"], ["tst-ch01-s01", "tst-ch02"])
        self.assertEqual(r["polarity_evidence"], "نادرست است")

    def test_polarity_null_drops_evidence(self):
        r = validate_classify_payload(good_cls(polarity=None, polarity_evidence="x"), TOPICS)
        self.assertIsNone(r["polarity"])
        self.assertIsNone(r["polarity_evidence"])

    def test_rejections(self):
        bads = [
            good_cls(structure="weird"), good_cls(question_type="weird"), good_cls(route="x"),
            good_cls(context_dependency="x"), good_cls(polarity="maybe"), good_cls(polarity_evidence=""),
            good_cls(topics={"main": "invented-topic", "secondary": []}),
            good_cls(topics={"main": "tst-ch01-s01", "secondary": ["tst-ch01-s02", "tst-ch02", "tst-ch01-s01"]}),
            good_cls(topics={"main": "tst-ch01-s01", "secondary": ["tst-ch01-s01"]}),
            good_cls(topics={"main": "tst-ch01-s01", "secondary": ["nope"]}),
            good_cls(topics="tst-ch01-s01"), good_cls(llm_confidence=1.5), good_cls(llm_confidence="high"),
            good_cls(llm_confidence=True), [], "x"]
        for b in bads:
            with self.assertRaises(ValueError, msg=str(b)):
                validate_classify_payload(b, TOPICS)


def rec(**kw):
    r = {"stem_original": "کدام عبارت درباره‌ی موجود فرضی نادرست است؟",
         "options": [{"label": str(i), "text_original": f"عبارت {i}"} for i in range(1, 5)], "figure_ids": []}
    r.update(kw)
    return r


class ClassificationReasons(unittest.TestCase):
    def reasons(self, r, **cls_kw):
        return classification_reasons(r, validate_classify_payload(good_cls(**cls_kw), TOPICS))

    def test_clean(self):
        self.assertEqual(self.reasons(rec()), [])

    def test_evidence_not_in_stem(self):
        self.assertIn("polarity_evidence_not_found", self.reasons(rec(), polarity_evidence="عبارتی که نیست"))

    def test_polarity_disagrees_with_stem_markers(self):
        r = self.reasons(rec(), polarity="positive", polarity_evidence="نادرست است")
        self.assertIn("polarity_disagreement", r)

    def test_polarity_missing_for_statement(self):
        self.assertIn("polarity_missing", self.reasons(rec(), polarity=None))
        self.assertNotIn("polarity_missing", self.reasons(rec(), polarity=None, route="problem", structure="calculation"))

    def test_count_structure_mismatch(self):
        r = rec(stem_original="چند مورد نادرست است؟",
                options=[{"label": "1", "text_original": "الف و ب"}, {"label": "2", "text_original": "۲"}])
        self.assertIn("structure_option_mismatch", self.reasons(r, structure="count_items"))

    def test_route_structure_mismatch(self):
        self.assertIn("route_structure_mismatch", self.reasons(rec(), structure="calculation", route="statement"))
        self.assertIn("route_structure_mismatch", self.reasons(rec(), structure="single_best_statement", route="problem"))

    def test_figure_context(self):
        self.assertIn("figure_context_mismatch", self.reasons(rec(figure_ids=["f1"])))
        self.assertIn("figure_context_mismatch", self.reasons(rec(), context_dependency="figure"))
        self.assertNotIn("figure_context_mismatch", self.reasons(rec(figure_ids=["f1"]), context_dependency="figure"))

    def test_llm_confidence_never_approves(self):
        out = apply_classification(rec(), validate_classify_payload(good_cls(llm_confidence=1.0), TOPICS))
        self.assertNotIn("review_status", out)  # تأیید فقط از بازبین انسانی می‌آید


if __name__ == "__main__":
    unittest.main()
