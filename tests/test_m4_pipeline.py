# -*- coding: utf-8 -*-
"""یکپارچه‌ی M4: validate، export، review، merge، dedupe بین سال‌ها، با Mock و PDF ساختگی."""
import csv
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from exambank.cli import build_parser, run_pipeline
from exambank.client import CachedClient, MockLLMClient
from exambank.dedupe import CONFLICT
from exambank.records import Problem, Question, Statement, validate
from exambank.review import STALE, fingerprint
from exambank.stages.validate import validate_project
from exambank.taxonomy import load_taxonomy
from exambank.utils import read_json, read_jsonl, write_jsonl, write_json
from tests.test_extract_stage import make_pdf
from tests.test_m3_pipeline import Responder
from tests.test_taxonomy_classify import FAKE_TAX


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class Base(unittest.TestCase):
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

    def tearDown(self):
        self.tmp.cleanup()

    def proj(self, name="proj"):
        return self.d / name

    def cmd(self, command, *extra, name="proj", pdf=True):
        argv = [command, "-n", name, "-o", str(self.d), *extra]
        if pdf:
            argv += ["-i", str(self.pdf), "-p", "1-4"]
        return run_pipeline(build_parser().parse_args(argv), client_factory=self.factory, log=self.logs.append,
                            sleep=lambda s: None)

    def run_project(self, name="proj", exam="exam", year="1402", *extra):
        return self.cmd("run-all", "--year", year, "--group", "تجربی", "--exam-id", exam, "--taxonomy", str(self.tax),
                        *extra, name=name)

    def calls(self):
        return len(self.mock.calls)

    def write_review_csv(self, rows, path=None):
        path = path or self.d / "review.csv"
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=["id", "decision", "truth_new", "text_new", "note"], lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
        Path(path).write_bytes(("\ufeff" + buf.getvalue()).encode("utf-8"))
        return path

    def stmts(self, name="proj"):
        return {s["statement_id"]: s for s in read_jsonl(self.proj(name) / "statements.jsonl")}


class FinalOutputs(Base):
    def test_contract_and_files(self):
        self.assertEqual(self.run_project(), 0, "\n".join(self.logs))
        P = self.proj()
        for f in ("questions.jsonl", "statements.jsonl", "problems.jsonl", "statements.csv", "review_queue.csv",
                  "SCHEMA.md", "state.json", "export_report.json", "validation_report.json",
                  "schema/statement.schema.json", "schema/problem.schema.json", "schema/question.schema.json"):
            self.assertTrue((P / f).exists(), f)
        self.assertFalse((P / "bank.sqlite").exists())               # اختیاری
        stmts, probs, qs = read_jsonl(P / "statements.jsonl"), read_jsonl(P / "problems.jsonl"), read_jsonl(P / "questions.jsonl")
        self.assertEqual((len(stmts), len(probs), len(qs)), (16, 2, 5))
        for cls, recs in ((Statement, stmts), (Problem, probs), (Question, qs)):
            for r in recs:
                self.assertEqual(validate(cls, r), [])
        for s in stmts:                                              # تعریف «تمام‌شده»
            self.assertEqual(s["truth"] is None, s["truth_basis"] == "unresolved")
            src = s["source"]
            self.assertTrue(src["exam_id"] and src["year_jalali"] == 1402 and src["group"] == "تجربی"
                            and src["question_no"] and src["pages"]["question"])
            self.assertEqual(s["dedupe"]["canonical_id"] in {x["statement_id"] for x in stmts}, True)
        rep = read_json(P / "export_report.json")
        self.assertEqual((rep["statements_truth_known"], rep["statements_needs_review"], rep["rejected_export"]), (16, 0, 0))
        self.assertEqual(read_json(P / "validation_report.json")["rejected"], 0)
        for name in ("statement", "problem", "question"):
            sch = read_json(P / "schema" / f"{name}.schema.json")
            self.assertEqual(sch["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("## Statement", (P / "SCHEMA.md").read_text(encoding="utf-8"))
        self.assertTrue((P / "statements.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        with open(P / "statements.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["year_jalali"], "1402")
        self.assertEqual({r["truth"] for r in rows}, {"true", "false"})

    def test_questions_jsonl_has_spec_fields(self):
        self.run_project()
        q = next(r for r in read_jsonl(self.proj() / "questions.jsonl") if r["number"] == 1)
        for k in ("exam", "number", "stem_original", "stem", "options", "correct", "answer_status", "answer_source",
                  "explanation_original", "question_type", "structure", "polarity", "polarity_evidence", "route",
                  "topic_ids", "figure_ids", "context_dependency", "pages"):
            self.assertIn(k, q)
        self.assertEqual((q["answer_source"], q["correct"]), ("both", ["3"]))

    def test_sqlite(self):
        self.run_project("proj", "exam", "1402", "--sqlite")
        con = sqlite3.connect(self.proj() / "bank.sqlite")
        self.assertEqual(con.execute("select count(*) from statements").fetchone()[0], 16)
        self.assertEqual(con.execute("select count(*) from problems").fetchone()[0], 2)
        self.assertEqual(con.execute("select count(*) from questions").fetchone()[0], 5)
        self.assertEqual(dict(con.execute("select truth, count(*) from statements group by truth").fetchall()), {0: 10, 1: 6})
        js = json.loads(con.execute("select json from statements where statement_id='exam_q001_o3'").fetchone()[0])
        self.assertEqual(js, self.stmts()["exam_q001_o3"])
        self.assertEqual(con.execute("select topic_main from statements limit 1").fetchone()[0], "tst-ch01-s01")
        con.close()

    def test_year_missing_warns_and_flags(self):
        a = build_parser().parse_args(["run-all", "-n", "proj", "-o", str(self.d), "-i", str(self.pdf), "-p", "1-4",
                                       "--exam-id", "exam", "--taxonomy", str(self.tax)])
        self.assertEqual(run_pipeline(a, client_factory=self.factory, log=self.logs.append, sleep=lambda s: None), 0)
        self.assertTrue(any("سال شمسی" in m for m in self.logs))
        s = self.stmts()["exam_q001_o1"]
        self.assertIn("source_missing_year", s["review_reasons"])
        self.assertTrue(s["needs_review"])

    def test_missing_prerequisites_give_clear_errors(self):
        self.assertEqual(self.cmd("export", pdf=False), 1)
        self.assertTrue(any("validate" in m for m in self.logs))
        self.logs.clear()
        self.assertEqual(self.cmd("validate", pdf=False), 1)
        self.assertTrue(any("classify" in m or "atomize" in m for m in self.logs))


class ReviewFlow(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_project(), 0, "\n".join(self.logs))
        self.csv = self.write_review_csv([
            {"id": "exam_q001_o1", "decision": "", "truth_new": "false", "text_new": "", "note": "طبق کتاب"},
            {"id": "exam_q001_o2", "decision": "rejected"},
            {"id": "exam_q004_prob", "decision": "approved"},
            {"id": "exam_q002_i1", "decision": "", "truth_new": "", "text_new": "مورد یک درباره‌ی موجود فرضی ب برقرار است"}])

    def import_and_export(self):
        self.assertEqual(self.cmd("import-review", "--review-csv", str(self.csv), pdf=False), 0, "\n".join(self.logs))
        self.assertEqual(self.cmd("export", pdf=False), 0)

    def check_edits_applied(self):
        s = self.stmts()
        self.assertEqual((s["exam_q001_o1"]["truth"], s["exam_q001_o1"]["truth_basis"], s["exam_q001_o1"]["review_status"]),
                         (False, "manual", "edited"))
        self.assertEqual(s["exam_q001_o1"]["review"]["previous_truth"], True)
        self.assertEqual(s["exam_q001_o1"]["review"]["note"], "طبق کتاب")
        self.assertNotIn("exam_q001_o2", s)                              # رد‌شده از خروجی حذف شد
        self.assertIn("exam_q001_o2", {r["statement_id"] for r in read_jsonl(self.proj() / "rejected_by_review.jsonl")})
        self.assertEqual(s["exam_q002_i1"]["text_standalone"], "مورد یک درباره‌ی موجود فرضی ب برقرار است")
        self.assertEqual(s["exam_q002_i1"]["truth"], True)                # فقط متن عوض شد؛ truth از کلید می‌ماند
        self.assertEqual(s["exam_q002_i1"]["truth_basis"], "key_derived")
        p = {p["problem_id"]: p for p in read_jsonl(self.proj() / "problems.jsonl")}["exam_q004_prob"]
        self.assertEqual(p["review_status"], "approved")
        self.assertEqual(len(s), 15)
        for r in s.values():
            self.assertEqual(validate(Statement, r), [])

    def test_edits_applied(self):
        self.import_and_export()
        self.check_edits_applied()

    def test_edits_survive_full_rerun_without_llm_calls(self):
        """هسته‌ی نیاز: اجرای دوباره‌ی مراحل نباید ویرایش دستی را بازنویسی کند."""
        self.import_and_export()
        before = self.calls()
        self.assertEqual(self.run_project("proj", "exam", "1402", "--restart"), 0)
        self.assertEqual(self.calls(), before)
        self.check_edits_applied()
        self.assertEqual(read_json(self.proj() / "export_report.json")["stale_review_edits"], 0)

    def test_edits_file_is_separate_and_persistent(self):
        self.import_and_export()
        edits = {e["id"]: e for e in read_jsonl(self.proj() / "review_edits.jsonl")}
        self.assertEqual(set(edits), {"exam_q001_o1", "exam_q001_o2", "exam_q004_prob", "exam_q002_i1"})
        self.assertEqual(edits["exam_q001_o1"]["patch"], {"truth": False})
        self.assertEqual(edits["exam_q001_o2"]["decision"], "rejected")

    def test_stale_edit_flagged_not_applied(self):
        self.import_and_export()
        base = self.proj() / "validated" / "statements.jsonl"
        recs = read_jsonl(base)
        for r in recs:
            if r["statement_id"] == "exam_q001_o1":
                r["text_original"] = "متن بعد از استخراج مجدد عوض شد"
        write_jsonl(base, recs)
        self.assertEqual(self.cmd("export", pdf=False), 0)
        s = self.stmts()["exam_q001_o1"]
        self.assertEqual((s["truth"], s["review_status"]), (True, "pending"))   # حکم قدیمی روی متن جدید اعمال نشد
        self.assertIn(STALE, s["review_reasons"])
        self.assertTrue(s["needs_review"])
        self.assertEqual(read_json(self.proj() / "export_report.json")["stale_review_edits"], 1)
        rows = read_csv(self.proj() / "review_queue.csv")
        self.assertIn("exam_q001_o1", [r["id"] for r in rows])

    def test_import_errors_do_not_block_valid_rows(self):
        path = self.write_review_csv([{"id": "exam_q001_o1", "truth_new": "false"}, {"id": "nope", "decision": "approved"},
                                      {"id": "exam_q001_o3", "truth_new": "شاید"}])
        self.assertEqual(self.cmd("import-review", "--review-csv", str(path), pdf=False), 1)
        self.assertEqual(self.cmd("export", pdf=False), 0)
        self.assertIs(self.stmts()["exam_q001_o1"]["truth"], False)
        self.assertEqual(sum("ERROR" in m for m in self.logs), 2)

    def test_import_requires_csv(self):
        self.assertEqual(self.cmd("import-review", pdf=False), 2)


class ValidateStage(Base):
    def test_rejections_and_flags(self):
        self.assertEqual(self.run_project("proj", "exam", "1402", "--stages", "extract,assemble,classify,atomize"), 0)
        P = self.proj()
        st = read_jsonl(P / "statements_raw.jsonl")
        by = {s["statement_id"]: s for s in st}
        by["exam_q001_o1"].update(truth=True, truth_basis="unresolved")                  # truth بدون basis
        by["exam_q001_o2"].pop("source")                                                 # source ناقص
        by["exam_q001_o3"]["question_id"] = "ghost_q999"                                 # یتیم
        by["exam_q001_o4"]["text_standalone"] = "  "                                     # متن خالی
        dup = dict(by["exam_q002_i1"])
        st.append(dup)                                                                    # id تکراری
        by["exam_q003_i1"].update(truth=True, truth_basis="explanation", truth_evidence="")  # explanation بدون evidence
        write_jsonl(P / "statements_raw.jsonl", st)
        qs = read_jsonl(P / "classified.jsonl")
        for q in qs:
            if q["number"] == 2:
                q["correct"] = ["9"]                                                      # کلید بیرون از گزینه‌ها
            if q["number"] == 3:
                q["exam"]["group"] = None
        write_jsonl(P / "classified.jsonl", qs)
        rep = read_json(P / "assemble_report.json")
        rep["issues"].append({"code": "unusual_option_count", "exam_id": "exam", "number": 5, "pages": [2], "detail": "5"})
        rep["issues"].append({"code": "dangling_continuation", "exam_id": None, "number": 4, "pages": [2], "detail": "q"})
        rep["issues"].append({"code": "missing_question_numbers", "exam_id": "exam", "number": None, "pages": [], "detail": "[9]"})
        write_json(P / "assemble_report.json", rep)

        report = validate_project(P, load_taxonomy(self.tax), log=self.logs.append)
        rejected = {r["id"]: r["errors"] for r in read_jsonl(P / "rejected_invalid.jsonl")}
        self.assertEqual(set(rejected), {"exam_q001_o1", "exam_q001_o2", "exam_q001_o3", "exam_q001_o4", "exam_q002_i1",
                                         "exam_q003_i1"})
        self.assertTrue(any("basis" in e for e in rejected["exam_q001_o1"]))
        self.assertTrue(any("source" in e for e in rejected["exam_q001_o2"]))
        self.assertTrue(any("ghost_q999" in e for e in rejected["exam_q001_o3"]))
        self.assertTrue(any("خالی" in e for e in rejected["exam_q001_o4"]))
        self.assertTrue(any("evidence" in e for e in rejected["exam_q003_i1"]))
        self.assertEqual(report["statements"], {"in": 17, "valid": 11})
        good = {s["statement_id"]: s for s in read_jsonl(P / "validated" / "statements.jsonl")}
        self.assertIn("exam_q002_i3", good)                                              # بدون ایراد ماند (نمونه‌ی تکراری فقط اولی)
        # پرچم‌های سطح سوال به گزاره‌های همان سوال می‌رسد
        self.assertIn("key_not_in_options", good["exam_q002_i2"]["review_reasons"])
        self.assertIn("source_missing_group", good["exam_q003_i2"]["review_reasons"])
        self.assertIn("asm_unusual_option_count", good["exam_q005_o1"]["review_reasons"])
        self.assertIn("asm_dangling_continuation", {r for p in read_jsonl(P / "validated" / "problems.jsonl")
                                                    if p["question_id"] == "exam_q004" for r in p["review_reasons"]})
        self.assertTrue(good["exam_q005_o1"]["needs_review"])
        self.assertNotIn("asm_missing_question_numbers", json.dumps(report))             # مشکل سطح آزمون روی گزاره نمی‌رود
        self.assertFalse(report["pydantic"]["available"] and report["pydantic"]["disagreements"])

    def test_topic_outside_taxonomy_flagged(self):
        self.run_project("proj", "exam", "1402", "--stages", "extract,assemble,classify,atomize")
        P = self.proj()
        tax = {"version": 1, "books": [dict(FAKE_TAX["books"][0], chapters=[
            {"chapter_id": "tst-ch02", "number": 2, "title": "فقط این", "sections": []}])]}
        validate_project(P, tax, log=lambda m: None)
        s = read_jsonl(P / "validated" / "statements.jsonl")[0]
        self.assertIn("topic_not_in_taxonomy", s["review_reasons"])


class MergeFlow(Base):
    def make_two_years(self):
        self.assertEqual(self.run_project("a", "exam", "1402"), 0, "\n".join(self.logs))
        n = self.calls()
        self.assertEqual(self.run_project("b", "exam2", "1403"), 0, "\n".join(self.logs))
        self.assertEqual(self.calls(), n)         # پروژه‌ی دوم کاملاً از کش (PDF و پاسخ‌ها یکسان)

    def merge(self, *extra):
        return self.cmd("merge", "--from", str(self.proj("a")), str(self.proj("b")), *extra, name="master", pdf=False)

    def test_upsert_dedupe_across_years(self):
        self.make_two_years()
        self.assertEqual(self.merge(), 0, "\n".join(self.logs))
        M = "master"
        s = self.stmts(M)
        self.assertEqual((len(s), len(read_jsonl(self.proj(M) / "problems.jsonl")),
                          len(read_jsonl(self.proj(M) / "questions.jsonl"))), (32, 4, 10))
        q1a, q1b = s["exam_q001_o1"], s["exam2_q001_o1"]
        for r in (q1a, q1b):
            self.assertEqual((r["dedupe"]["canonical_id"], r["dedupe"]["occurrences"], r["dedupe"]["years"], r["dedupe"]["match"]),
                             ("exam_q001_o1", 2, [1402, 1403], "exact"))
        self.assertEqual(s["exam_q002_i1"]["dedupe"]["occurrences"], 1)    # آیتم بدون زمینه با سوال دیگر یکی نمی‌شود
        self.assertEqual(s["exam_q005_o1"]["dedupe"]["occurrences"], 1)    # وابسته به شکل
        self.assertFalse(any(CONFLICT in r["review_reasons"] for r in s.values()))
        self.assertEqual(read_json(self.proj(M) / "export_report.json")["dedupe"]["duplicated_statements"], 8)
        # upsert: ادغام دوباره تکرار نمی‌سازد
        self.assertEqual(self.merge(), 0)
        self.assertEqual(len(self.stmts(M)), 32)
        self.assertEqual(len(read_jsonl(self.proj(M) / "questions.jsonl")), 10)
        # پروژه‌ها دست‌نخورده
        self.assertEqual(len(self.stmts("a")), 16)

    def test_cross_year_conflict_and_resolution_survives_remerge(self):
        self.make_two_years()
        B = self.proj("b") / "validated" / "statements.jsonl"
        recs = read_jsonl(B)
        for r in recs:
            if r["statement_id"] == "exam2_q001_o1":
                r["truth"] = False                                # شبیه‌سازی خطای استخراج در سال دوم
        write_jsonl(B, recs)
        self.assertEqual(self.merge(), 0)
        s = self.stmts("master")
        self.assertIn(CONFLICT, s["exam_q001_o1"]["review_reasons"])
        self.assertIn(CONFLICT, s["exam2_q001_o1"]["review_reasons"])
        rows = read_csv(self.proj("master") / "review_queue.csv")
        self.assertEqual({r["id"] for r in rows if CONFLICT in r["reasons"]}, {"exam_q001_o1", "exam2_q001_o1"})
        # بازبین سال دوم را اصلاح می‌کند (روی بانک اصلی)
        csv_path = self.write_review_csv([{"id": "exam2_q001_o1", "truth_new": "true", "note": "خطای استخراج"}])
        self.assertEqual(self.cmd("import-review", "--review-csv", str(csv_path), name="master", pdf=False), 0)
        self.assertEqual(self.cmd("export", name="master", pdf=False), 0)
        s = self.stmts("master")
        self.assertEqual((s["exam2_q001_o1"]["truth"], s["exam2_q001_o1"]["truth_basis"]), (True, "manual"))
        self.assertFalse(CONFLICT in s["exam_q001_o1"]["review_reasons"] or CONFLICT in s["exam2_q001_o1"]["review_reasons"])
        self.assertFalse(s["exam_q001_o1"]["needs_review"])
        # ادغام دوباره‌ی پروژه‌ها (که هنوز خطا را دارند) ویرایش بازبین را از بین نمی‌برد
        self.assertEqual(self.merge(), 0)
        s = self.stmts("master")
        self.assertEqual((s["exam2_q001_o1"]["truth"], s["exam2_q001_o1"]["review_status"]), (True, "edited"))
        self.assertNotIn(CONFLICT, s["exam_q001_o1"]["review_reasons"])

    def test_merge_edits_from_project_carry_over(self):
        self.make_two_years()
        csv_path = self.write_review_csv([{"id": "exam_q001_o2", "decision": "rejected"}])
        self.assertEqual(self.cmd("import-review", "--review-csv", str(csv_path), name="a", pdf=False), 0)
        self.assertEqual(self.merge(), 0)
        s = self.stmts("master")
        self.assertNotIn("exam_q001_o2", s)
        self.assertIn("exam2_q001_o2", s)
        self.assertEqual(s["exam2_q001_o2"]["dedupe"]["occurrences"], 1)        # رد‌شده از خوشه بیرون است

    def test_merge_requires_validated_projects(self):
        self.assertEqual(self.run_project("a", "exam", "1402", "--stages", "extract,assemble"), 0)
        self.assertEqual(self.cmd("merge", "--from", str(self.proj("a")), name="master", pdf=False), 1)
        self.assertTrue(any("validate" in m for m in self.logs))
        self.assertEqual(self.cmd("merge", name="master", pdf=False), 2)


if __name__ == "__main__":
    unittest.main()
