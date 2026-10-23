# -*- coding: utf-8 -*-
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from exambank.cache import DiskCache
from exambank.cli import build_parser, run_pipeline
from exambank.client import CachedClient, LLMInvalidResponse, MockLLMClient
from exambank.figures import bbox_to_pixels, render_page, save_figures
from exambank.schemas import validate_page_payload
from exambank.state import StateStore
from exambank.utils import read_json, read_jsonl

OPT = lambda t: [{"label": str(i), "text": f"{t} {i}", "is_continuation": False} for i in range(1, 5)]


def make_pdf(path, n_pages=4):
    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(1, n_pages + 1):
        c.drawString(100, 780, f"PAGE-{i}")
        if i == 4:
            c.rect(100, 300, 300, 200, fill=1)  # «شکل»
        c.showPage()
    c.save()


def q(number, stem, opts, cfp=False, cnp=False):
    return {"number": number, "stem": stem, "options": opts, "continues_from_previous_page": cfp,
            "continues_on_next_page": cnp}


def a(number, correct, status=None, expl=None):
    return {"number": number, "correct": correct, "status": status, "explanation": expl,
            "continues_from_previous_page": False, "continues_on_next_page": False}


PAGES = {
    "PAGE-1": {"page_role": "questions", "questions": [
        q(1, "سوال اول با ژنوتیپ X^A X^a و Na+", OPT("گزینه")),
        q(2, "چند مورد", [], cnp=True)], "answers": [], "figures": []},
    "PAGE-2": {"page_role": "questions", "questions": [
        q(None, "نادرست است؟", OPT("مورد"), cfp=True), q(3, "سوال شکل‌دار", OPT("شکل"))],
        "answers": [], "figures": [{"kind": "figure", "description_fa": "مستطیل سیاه", "labels_in_figure": ["الف"],
                                    "box_2d": [300, 200, 700, 800], "question_numbers": [3]}]},
    "PAGE-3": {"page_role": "answer_key", "questions": [], "answers": [
        a(1, ["2"]), a(2, ["3"]), a(3, [], status="annulled")], "figures": []},
    "PAGE-4": {"page_role": "other", "questions": [], "answers": [], "figures": []},
}


def responder(task, parts):
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(parts[0][1])).pages[0].extract_text()  # واقعاً صفحه‌ی تک‌صفحه‌ای را می‌خواند
    for marker, resp in PAGES.items():
        if marker in text:
            assert len(PdfReader(io.BytesIO(parts[0][1])).pages) == 1
            return json.loads(json.dumps(resp))
    raise AssertionError("صفحه‌ی ناشناخته")


class ValidatorTests(unittest.TestCase):
    def ok(self, **kw):
        d = {"page_role": "questions", "questions": [], "answers": [], "figures": []}
        d.update(kw)
        return validate_page_payload(d)

    def bad(self, data):
        with self.assertRaises(ValueError):
            validate_page_payload(data)

    def test_empty_page_with_explicit_lists_is_valid(self):
        r = self.ok(page_role="other")
        self.assertEqual((r["questions"], r["answers"], r["figures"]), ([], [], []))

    def test_missing_lists_or_placeholder_is_failure(self):  # باگ (ب)
        self.bad({"page_role": "other"})
        self.bad({"page_role": "other", "questions": "[هیچ سوالی در این صفحه یافت نشد]", "answers": [], "figures": []})
        self.bad({"page_role": "questions", "questions": [q(1, "[هیچ سوالی در این صفحه یافت نشد]", [])],
                  "answers": [], "figures": []})
        self.bad({})
        self.bad([])
        self.bad("متن")

    def test_bad_role(self):
        self.bad({"page_role": "x", "questions": [], "answers": [], "figures": []})

    def test_normalizes_numbers_and_labels(self):
        r = self.ok(questions=[q("۱۲", "s", [{"label": "۱)", "text": "t", "is_continuation": False}])])
        self.assertEqual((r["questions"][0]["number"], r["questions"][0]["options"][0]["label"]), (12, "1"))

    def test_original_text_not_altered_except_strip(self):
        r = self.ok(questions=[q(1, "  يك  ك ", OPT("x"))])
        self.assertEqual(r["questions"][0]["stem"], "يك  ك")

    def test_question_without_number_needs_continuation(self):
        self.bad({"page_role": "questions", "questions": [q(None, "s", OPT("x"))], "answers": [], "figures": []})
        self.ok(questions=[q(None, "s", OPT("x"), cfp=True)])

    def test_continuation_only_first(self):
        self.bad({"page_role": "questions", "questions": [q(1, "s", OPT("x")), q(None, "s", OPT("x"), cfp=True)],
                  "answers": [], "figures": []})

    def test_empty_stem_and_options_rejected(self):
        self.bad({"page_role": "questions", "questions": [q(1, "", [])], "answers": [], "figures": []})

    def test_empty_option_text_rejected_unless_continuation(self):
        self.bad({"page_role": "questions", "questions": [q(1, "s", [{"label": "1", "text": "", "is_continuation": False}])],
                  "answers": [], "figures": []})

    def test_answer_rules(self):
        self.ok(answers=[a(1, ["۳"])])
        self.ok(answers=[a(1, [], status="annulled")])
        self.bad({"page_role": "answer_key", "questions": [], "answers": [a(1, [])], "figures": []})
        self.bad({"page_role": "answer_key", "questions": [], "answers": [a(1, ["1"], status="weird")], "figures": []})

    def test_figure_rules(self):
        f = {"kind": "graph", "description_fa": "x", "labels_in_figure": [], "box_2d": None, "question_numbers": [1]}
        self.ok(figures=[f])
        self.bad({"page_role": "questions", "questions": [], "answers": [], "figures": [dict(f, kind="movie")]})
        self.bad({"page_role": "questions", "questions": [], "answers": [], "figures": [dict(f, box_2d=[1, 2, 3])]})


class FigureTests(unittest.TestCase):
    def test_bbox_conversion_ymin_xmin_order(self):
        # [ymin, xmin, ymax, xmax]؛ بدون حاشیه‌ی ۱٪ مقایسه نمی‌کنیم، با آن مقایسه می‌کنیم
        px = bbox_to_pixels([200, 100, 600, 500], 1000, 2000)
        self.assertEqual(px, (90, 380, 510, 1220))

    def test_invalid_boxes(self):
        for b in (None, [1, 2, 3], [600, 100, 200, 500], [0, 0, 5, 5], [-1, 0, 500, 500], [0, 0, 1001, 500],
                  "abc", [0, 0, "x", 500]):
            self.assertIsNone(bbox_to_pixels(b, 100, 100), b)

    def test_crop_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "t.pdf"
            make_pdf(pdf)
            figs = [{"kind": "figure", "description_fa": "x", "labels_in_figure": [], "box_2d": [300, 200, 700, 800],
                     "question_numbers": [3]},
                    {"kind": "table", "description_fa": "y", "labels_in_figure": [], "box_2d": None,
                     "question_numbers": []}]
            out = save_figures(pdf, "t", 4, figs, Path(d) / "figures", scale=1.0)
            self.assertEqual([f["figure_id"] for f in out], ["t-p4-f1", "t-p4-f2"])
            self.assertEqual([f["bbox_status"] for f in out], ["cropped", "full_page_fallback"])
            self.assertTrue(all(f["is_ai_description"] for f in out))
            from PIL import Image
            with Image.open(Path(d) / out[1]["image_path"]) as full, Image.open(Path(d) / out[0]["image_path"]) as crop:
                self.assertLess(crop.size[0], full.size[0])
                self.assertLess(crop.size[1], full.size[1])
                self.assertEqual(full.size, render_page(pdf, 4, 1.0).size)

    def test_crop_orientation_matches_real_pixels(self):
        """مستطیل سیاه PDF: x از 100 تا 400، y (از پایین) 300 تا 500 روی A4 (595.3×841.9).
        معادل [ymin, xmin, ymax, xmax] در مقیاس 1000 ← y: 406..644، x: 168..672.
        برش درست باید تقریباً کاملاً سیاه باشد؛ اگر x و y جابه‌جا خوانده شوند سفید می‌شود."""
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "t.pdf"
            make_pdf(pdf)
            tight = [{"kind": "figure", "description_fa": "", "labels_in_figure": [],
                      "box_2d": [415, 175, 635, 665], "question_numbers": []}]
            swapped = [{"kind": "figure", "description_fa": "", "labels_in_figure": [],
                        "box_2d": [175, 415, 665, 635], "question_numbers": []}]
            from PIL import Image
            out = save_figures(pdf, "t", 4, tight, Path(d) / "f1", scale=1.0)
            with Image.open(Path(d) / "f1" / Path(out[0]["image_path"]).name) as im:
                px = list(im.convert("L").getdata())
            dark = sum(1 for v in px if v < 60) / len(px)
            self.assertGreater(dark, 0.95)
            out = save_figures(pdf, "t", 4, swapped, Path(d) / "f2", scale=1.0)
            with Image.open(Path(d) / "f2" / Path(out[0]["image_path"]).name) as im:
                px = list(im.convert("L").getdata())
            self.assertLess(sum(1 for v in px if v < 60) / len(px), 0.5)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.pdf = self.d / "exam.pdf"
        make_pdf(self.pdf)
        self.mock = MockLLMClient(responder)
        self.factory = lambda cfg, cache: CachedClient(self.mock, cache, "m")
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra):
        return build_parser().parse_args(
            ["-n", "proj", "-i", str(self.pdf), "-p", "1-4", "-o", str(self.d), "--year", "1403",
             "--group", "تجربی", "--exam-id", "bio-1403-t", *extra])

    def run_it(self, *extra, command=None):
        a = self.args(*extra)
        if command:
            a.command = command
        return run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None)

    def test_run_all_end_to_end(self):
        code = self.run_it(command="run-all")
        self.assertEqual(code, 0, self.logs)
        self.assertEqual(len(self.mock.calls), 4)
        proj = self.d / "proj"
        recs = read_jsonl(proj / "assembled.jsonl")
        self.assertEqual([r["number"] for r in recs], [1, 2, 3])
        r1, r2, r3 = recs
        self.assertEqual(r1["stem_original"], "سوال اول با ژنوتیپ X^A X^a و Na+")  # عین متن
        self.assertEqual(r1["stem"], "سوال اول با ژنوتیپ X^{A} X^{a} و Na⁺")      # نرمال‌شده
        self.assertEqual(r1["correct"], ["2"])
        self.assertEqual(r2["stem_original"], "چند مورد نادرست است؟")             # سوارشده
        self.assertEqual(r2["pages"]["question"], [1, 2])
        self.assertEqual(len(r2["options"]), 4)
        self.assertEqual(r3["answer_status"], "annulled")
        self.assertEqual(r3["figure_ids"], ["exam-p2-f1"])
        self.assertEqual(r1["exam"]["year_jalali"], 1403)
        self.assertTrue((proj / "figures" / "exam_p0002_f1.png").exists())
        self.assertTrue((proj / "state.json").exists())
        report = read_json(proj / "assemble_report.json")
        self.assertEqual(report["questions"], 3)
        self.assertNotIn("question_without_answer", report["issue_counts"])

    def test_second_run_makes_no_llm_calls(self):
        self.run_it(command="run-all")
        n = len(self.mock.calls)
        self.assertEqual(self.run_it(command="run-all"), 0)
        self.assertEqual(len(self.mock.calls), n)

    def test_restart_uses_cache_not_quota(self):
        self.run_it(command="run-all")
        n = len(self.mock.calls)
        self.assertEqual(self.run_it("--restart", command="run-all"), 0)
        self.assertEqual(len(self.mock.calls), n)  # state پاک شد ولی کش پاسخ داد
        self.assertEqual(len(read_jsonl(self.d / "proj" / "assembled.jsonl")), 3)

    def test_resume_from_state_not_docx(self):
        a = self.args()
        a.command, a.pages = "extract", "1-2"
        run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None)
        self.assertEqual(len(self.mock.calls), 2)
        # فایل docx را پاک می‌کنیم؛ resume نباید به آن وابسته باشد
        for f in (self.d / "proj").glob("*.docx"):
            f.unlink()
        a.pages = "1-4"
        run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None)
        self.assertEqual(len(self.mock.calls), 4)

    def test_truncated_or_empty_response_is_failure_and_not_saved(self):
        calls = {"n": 0}

        def bad(task, parts):
            calls["n"] += 1
            return {"page_role": "questions"}  # لیست‌ها غایب = نامعتبر
        self.mock = MockLLMClient(bad)
        code = self.run_it(command="extract")
        self.assertEqual(code, 1)
        self.assertEqual(list((self.d / "proj" / "pages").glob("*.json")) if (self.d / "proj" / "pages").exists() else [], [])
        st = StateStore(self.d / "proj" / "state.json")
        self.assertEqual(len(st.failed("extract")), 4)
        # بعد از رفع مشکل، فقط صفحه‌های ناموفق دوباره اجرا می‌شوند و کش خراب وجود ندارد
        self.mock = MockLLMClient(responder)
        self.assertEqual(self.run_it(command="extract"), 0)
        self.assertEqual(len(self.mock.calls), 4)

    def test_quota_stop_saves_state_and_skips_rest(self):
        from exambank.client import LLMQuotaExhausted

        def quota(task, parts):
            if len(self.mock.calls) >= 3:
                return LLMQuotaExhausted("RequestsPerDay")
            return responder(task, parts)
        self.mock = MockLLMClient(quota)
        code = self.run_it(command="run-all")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.mock.calls), 3)  # صفحه‌ی ۳ سهمیه خورد؛ ۴ اصلاً تلاش نشد
        self.assertIsNotNone(StateStore(self.d / "proj" / "state.json").quota)
        self.assertFalse((self.d / "proj" / "assembled.jsonl").exists())
        # روز بعد
        self.mock = MockLLMClient(responder)
        self.assertEqual(self.run_it(command="run-all"), 0)
        self.assertEqual(len(self.mock.calls), 2)  # فقط صفحه‌های ۳ و ۴

    def test_legacy_form_makes_readable_docx(self):
        a = self.args()
        self.assertIsNone(a.command)
        self.assertEqual(run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None), 0)
        doc = Document(str(self.d / "proj" / "proj.docx"))
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("--- سوالات صفحه 1 ---", text)
        self.assertIn("--- سوالات صفحه 4 ---", text)
        self.assertIn("سوال شکل‌دار", text)
        self.assertFalse((self.d / "proj" / "assembled.jsonl").exists())  # قدیمی = فقط extract + docx

    def test_page_outside_pdf_is_skipped(self):
        a = self.args()
        a.command, a.pages = "extract", "3-9"
        self.assertEqual(run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None), 0)
        self.assertEqual(len(self.mock.calls), 2)
        self.assertTrue(any("صفحه 5" in m for m in self.logs))

    def test_assemble_without_pages_is_clear_error(self):
        a = self.args()
        a.command = "assemble"
        self.assertEqual(run_pipeline(a, client_factory=self.factory, log=self.logs.append), 1)
        self.assertTrue(any("extract" in m for m in self.logs))

    def test_unknown_stage_rejected(self):
        self.assertEqual(self.run_it("--stages", "extract,classify", command="run-all"), 2)


if __name__ == "__main__":
    unittest.main()
