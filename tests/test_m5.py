# -*- coding: utf-8 -*-
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document

from exambank.cache import DiskCache
from exambank.cli import build_parser, main as cli_main
from exambank.client import CachedClient, MockLLMClient
from exambank.evaluate import evaluate, format_report, load_gold, run_evaluate
from exambank.gui import form_to_argv, needs_api_key
from exambank.utils import read_json, write_jsonl
from tests.test_m3_pipeline import Responder
from tests.test_m4_pipeline import Base

ROOT = Path(__file__).resolve().parent.parent


def S(sid, truth, basis="key_derived", review=False, structure="single_best_statement"):
    return {"statement_id": sid, "truth": truth, "truth_basis": basis if truth is not None or basis == "manual" else "unresolved",
            "needs_review": review, "review_reasons": ["r"] if review else [], "structure": structure}


def Q(qid, correct, **kw):
    d = {"question_id": qid, "correct": correct, "answer_status": "official", "structure": "completion", "polarity": "positive",
         "route": "statement", "question_type": "textbook_recall", "topic_ids": ["t1"], "context_dependency": "none"}
    d.update(kw)
    return d


class EvaluateMetrics(unittest.TestCase):
    def setUp(self):
        self.pred_s = [S("s1", True), S("s2", True, review=True), S("s3", False), S("s4", None, review=True), S("s5", None),
                       S("s6", True), S("s7", True, basis="manual"), S("s8", True), S("s9", True, review=True)]
        self.gold_s = {"s1": {"statement_id": "s1", "truth": True}, "s2": {"statement_id": "s2", "truth": False},
                       "s3": {"statement_id": "s3", "truth": True}, "s4": {"statement_id": "s4", "truth": True},
                       "s5": {"statement_id": "s5", "truth": None}, "s6": {"statement_id": "s6", "truth": None},
                       "s7": {"statement_id": "s7", "truth": False}, "sX": {"statement_id": "sX", "truth": True}}
        self.pred_q = [Q("q1", ["3"]), Q("q2", ["2"], structure="single_best_statement"), Q("q4", [], answer_status="annulled", polarity=None)]
        self.gold_q = {"q1": {"question_id": "q1", "correct": ["3"], "structure": "completion", "polarity": "positive", "topic_ids": ["t1"]},
                       "q2": {"question_id": "q2", "correct": ["1"], "structure": "completion"},
                       "q3": {"question_id": "q3", "correct": ["4"]},
                       "q4": {"question_id": "q4", "correct": [], "answer_status": "annulled", "polarity": None}}

    def test_numbers(self):
        r = evaluate(self.pred_q, self.pred_s, self.gold_q, self.gold_s)
        self.assertEqual((r["questions"]["gold"], r["questions"]["matched"], r["questions"]["missing_ids"]), (4, 3, ["q3"]))
        self.assertEqual((r["answer_link"]["evaluated"], r["answer_link"]["correct"], r["answer_link"]["accuracy"]), (3, 2, 0.6667))
        self.assertEqual(r["answer_link"]["errors"], [{"id": "q2", "gold": ["1"], "pred": ["2"]}])
        self.assertEqual(r["fields"]["structure"]["accuracy"], 0.5)           # q1 درست، q2 غلط
        self.assertEqual(r["fields"]["polarity"]["accuracy"], 1.0)           # q1 مثبت، q4 null=null
        self.assertEqual(r["fields"]["main_topic"]["accuracy"], 1.0)
        self.assertEqual(r["fields"]["answer_status"]["accuracy"], 1.0)
        t = r["truth"]
        self.assertEqual((t["gold_known"], t["correct"], t["wrong"], t["abstained"]), (4, 1, 2, 1))
        self.assertEqual((t["precision"], t["answer_coverage"], t["accuracy"]), (0.3333, 0.75, 0.25))
        self.assertEqual((t["gold_null"], t["null_agreed"], t["false_positive_on_gold_null"], t["excluded_manual"]), (2, 1, 1, 1))
        self.assertEqual(t["by_basis"], {"key_derived": {"correct": 1, "wrong": 2}, "unresolved": {"abstained": 1}})
        self.assertEqual([e["id"] for e in t["errors"] if e["gold"] is not None], ["s2", "s3"])
        self.assertEqual(r["statements"]["missing_ids"], ["sX"])
        rv = r["review"]
        self.assertEqual((rv["statements_total"], rv["needs_review"], rv["needs_review_rate"]), (9, 3, 0.3333))
        self.assertEqual((rv["error_catch_rate"], rv["false_alarm_rate"]), (0.5, 0.0))   # s2 پرچم داشت، s3 نه

    def test_report_text(self):
        txt = format_report(evaluate(self.pred_q, self.pred_s, self.gold_q, self.gold_s))
        for frag in ("دقت اتصال پاسخ: 66.7٪", "precision", "needs_review: 33.3٪", "سهم خطاهای truth که پرچم داشتند: 50.0٪"):
            self.assertIn(frag, txt)

    def test_fields_only_when_in_gold(self):
        r = evaluate(self.pred_q, [], {"q1": {"question_id": "q1", "correct": ["3"]}}, {})
        self.assertEqual(set(r["fields"]), set())
        self.assertIsNone(r["truth"]["precision"])
        self.assertIsNone(r["review"]["needs_review_rate"])

    def test_load_gold(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                load_gold(d)
            (Path(d) / "x.example").write_text('{"question_id": "q"}', encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                load_gold(d)                                            # *.example خوانده نمی‌شود
            write_jsonl(Path(d) / "a.jsonl", [{"question_id": "q1", "correct": ["1"]}])
            write_jsonl(Path(d) / "b.jsonl", [{"statement_id": "s1", "truth": True}])
            qs, ss = load_gold(d)
            self.assertEqual((list(qs), list(ss)), (["q1"], ["s1"]))

    def test_shipped_examples_are_not_loaded(self):
        with self.assertRaises(FileNotFoundError):
            load_gold(ROOT / "gold")


class EvaluateCli(Base):
    def gold(self, truths):
        d = self.d / "gold"
        d.mkdir(exist_ok=True)
        write_jsonl(d / "g.jsonl", [
            {"question_id": "exam_q001", "correct": ["3"], "structure": "single_best_statement", "polarity": "negative"},
            {"question_id": "exam_q002", "correct": ["1"], "structure": "combo_items", "polarity": "positive"},
            {"question_id": "exam_q003", "correct": ["4"], "structure": "count_items", "polarity": "negative"},
            *[{"statement_id": k, "truth": v} for k, v in truths.items()]])
        return d

    def test_perfect_then_with_errors_and_manual_exclusion(self):
        self.run_project()
        good = {"exam_q001_o1": True, "exam_q001_o2": True, "exam_q001_o3": False, "exam_q001_o4": True,
                "exam_q002_i1": True, "exam_q002_i2": False, "exam_q003_i1": False}
        gd = self.gold(good)
        self.assertEqual(self.cmd("evaluate", "--gold", str(gd), pdf=False), 0, "\n".join(self.logs))
        r = read_json(self.proj() / "evaluation_report.json")
        self.assertEqual((r["answer_link"]["accuracy"], r["fields"]["structure"]["accuracy"], r["fields"]["polarity"]["accuracy"]), (1.0, 1.0, 1.0))
        self.assertEqual((r["truth"]["correct"], r["truth"]["wrong"], r["truth"]["precision"]), (7, 0, 1.0))
        self.assertEqual(r["review"]["needs_review_rate"], 0.0)
        # gold اشتباهی (شبیه خطای خط‌لوله) و یک گزاره‌ی ویرایش‌شده‌ی انسانی
        bad = dict(good, exam_q001_o1=False)
        bad["exam_q002_i1"] = False
        self.gold(bad)
        csv_path = self.write_review_csv([{"id": "exam_q002_i1", "truth_new": "true"}])
        self.cmd("import-review", "--review-csv", str(csv_path), pdf=False)
        self.cmd("export", pdf=False)
        self.assertEqual(self.cmd("evaluate", "--gold", str(gd), pdf=False), 0)
        r = read_json(self.proj() / "evaluation_report.json")
        self.assertEqual((r["truth"]["correct"], r["truth"]["wrong"], r["truth"]["excluded_manual"]), (5, 1, 1))
        self.assertEqual([e["id"] for e in r["truth"]["errors"]], ["exam_q001_o1"])

    def test_errors(self):
        self.assertEqual(self.cmd("evaluate", "--gold", str(self.d / "nogold"), pdf=False), 1)
        self.run_project()
        self.assertEqual(self.cmd("evaluate", "--gold", str(self.d / "nogold"), pdf=False), 1)
        self.assertTrue(any("gold" in m for m in self.logs))


class GuiForm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = os.path.join(self.tmp.name, "a.pdf")
        Path(self.pdf).write_bytes(b"%PDF")
        self.v = {"pdf_path": self.pdf, "name": "Bio10.3", "pages": "250-255, 260", "output_dir": self.tmp.name,
                  "model": "gemini-3.5-flash-lite", "stages": ["extract"], "layout": "auto", "docx": True}

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_legacy_behavior(self):
        argv = form_to_argv(self.v)
        self.assertEqual(argv[:5], ["extract", "-n", "Bio10.3", "-o", self.tmp.name])
        for x in ("-i", self.pdf, "-p", "250-255, 260", "-m", "gemini-3.5-flash-lite", "--docx"):
            self.assertIn(x, argv)
        self.assertNotIn("--stages", argv)
        args = build_parser().parse_args(argv)
        self.assertEqual((args.command, args.docx, args.pages), ("extract", True, "250-255, 260"))
        self.assertTrue(needs_api_key(argv))

    def test_multi_stage_and_options(self):
        v = dict(self.v, stages=["export", "extract", "assemble"], layout="separate", manifest="m.yaml", exam_id="e1",
                 year="1403", group="تجربی", taxonomy="t.json", sqlite=True, restart=True, docx=False)
        argv = form_to_argv(v)
        self.assertEqual(argv[0], "run-all")
        self.assertEqual(argv[argv.index("--stages") + 1], "extract,assemble,export")   # ترتیب ثابت
        args = build_parser().parse_args(argv)
        self.assertEqual((args.layout, args.manifest, args.exam_id, args.year, args.group, args.taxonomy),
                         ("separate", "m.yaml", "e1", 1403, "تجربی", "t.json"))
        self.assertTrue(args.sqlite and args.restart and not args.docx)

    def test_stages_without_llm_need_no_key_or_pdf(self):
        argv = form_to_argv(dict(self.v, stages=["validate", "export"], pdf_path="", pages=""))
        self.assertNotIn("-i", argv)
        self.assertFalse(needs_api_key(argv))
        self.assertTrue(needs_api_key(form_to_argv(dict(self.v, stages=["classify", "export"], pdf_path="", pages=""))))

    def test_validation_errors(self):
        bads = [dict(self.v, stages=[]), dict(self.v, name=""), dict(self.v, name="a/b"), dict(self.v, output_dir="/nonexistent"),
                dict(self.v, pdf_path="/nonexistent.pdf"), dict(self.v, pages="abc"), dict(self.v, pages=""),
                dict(self.v, year="14o3")]
        for b in bads:
            with self.assertRaises(ValueError, msg=str(b)):
                form_to_argv(b)


class Wrapper(unittest.TestCase):
    def run_py(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "Extraction.WorkFlow.py"), *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=60, cwd=str(ROOT), stdin=subprocess.DEVNULL)

    def test_help_and_legacy_errors(self):
        r = self.run_py("-h")
        self.assertEqual(r.returncode, 0)
        for flag in ("-n", "-m", "-i", "-p", "-o", "--restart"):
            self.assertIn(flag, r.stdout)
        r = self.run_py("-n", "x", "-i", "nofile.pdf", "-p", "1", "-o", tempfile.gettempdir())
        self.assertEqual(r.returncode, 2)
        self.assertIn("PDF", r.stderr)
        r = self.run_py("-n", "x", "-o", tempfile.gettempdir())
        self.assertEqual(r.returncode, 2)

    def test_python_dash_m_works(self):
        r = subprocess.run([sys.executable, "-m", "exambank", "-h"], capture_output=True, text=True, encoding="utf-8",
                           cwd=str(ROOT), timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0)

    def test_wrapper_is_thin(self):
        self.assertLess(len((ROOT / "Extraction.WorkFlow.py").read_text(encoding="utf-8").splitlines()), 25)


class LegacyCliThroughMain(Base):
    """CLI قدیمی (-n -m -i -p -o --restart) از مسیر main() با LLM جعلی: خروجی docx و خط [DONE] مثل قبل."""
    def run_legacy(self, *extra):
        cfg = self.d / "cfg.yaml"
        cfg.write_text("request_delay_seconds: 0\n", encoding="utf-8")
        extra = ("--config", str(cfg), *extra)
        client = CachedClient(self.mock, DiskCache(self.d / ".llm_cache"), "m")
        out = io.StringIO()
        with mock.patch("exambank.cli._make_client", return_value=client), contextlib.redirect_stdout(out):
            code = cli_main(["-n", "Bio10.3", "-m", "gemini-3.5-flash-lite", "-i", str(self.pdf), "-p", "1-4",
                             "-o", str(self.d), *extra])
        return code, out.getvalue()

    def test_legacy_form(self):
        code, out = self.run_legacy()
        self.assertEqual(code, 0, out)
        self.assertIn("[DONE] Finished. Word file:", out)
        docx = self.d / "Bio10.3" / "Bio10.3.docx"
        self.assertTrue(docx.exists())
        text = "\n".join(p.text for p in Document(str(docx)).paragraphs)
        self.assertIn("--- سوالات صفحه 1 ---", text)
        n = len(self.mock.calls)
        code, out = self.run_legacy()                                  # resume: بدون فراخوانی
        self.assertEqual((code, len(self.mock.calls)), (0, n))
        code, out = self.run_legacy("--restart")                        # restart: docx از نو، بدون مصرف سهمیه
        self.assertEqual((code, len(self.mock.calls)), (0, n))

    def test_cached_rerun_does_not_wait(self):
        """با تأخیر واقعی ۳ ثانیه‌ای، اجرای دوباره‌ی کاملاً کش‌شده نباید صبر کند."""
        import time
        self.run_legacy()
        self.assertEqual(len(self.mock.calls), 4)
        client = CachedClient(self.mock, DiskCache(self.d / ".llm_cache"), "m")
        out = io.StringIO()
        t0 = time.time()
        with mock.patch("exambank.cli._make_client", return_value=client), contextlib.redirect_stdout(out):
            code = cli_main(["-n", "Bio10.3", "-i", str(self.pdf), "-p", "1-4", "-o", str(self.d), "--restart"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertLess(time.time() - t0, 6)       # قبلاً ۳×۳ ثانیه صبر می‌کرد
        self.assertEqual(len(self.mock.calls), 4)

    def test_legacy_without_key_fails_cleanly(self):
        with mock.patch("exambank.keys.get_api_key_cli", return_value=None), contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["-n", "k", "-i", str(self.pdf), "-p", "1", "-o", str(self.d)])
        self.assertEqual(code, 1)
        self.assertIn("[DONE] Finished with errors", out.getvalue())


if __name__ == "__main__":
    unittest.main()
