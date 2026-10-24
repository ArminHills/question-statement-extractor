# -*- coding: utf-8 -*-
import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path

from exambank.review import (QUEUE_COLUMNS, STALE, apply_edits, build_edits_from_rows, fingerprint, load_edits,
                             merge_edits, parse_truth_cell, queue_rows, read_review_csv, save_edits, write_queue_csv)
from tests.test_records_dedupe import stmt


def prob(pid="a_q001_prob"):
    return {"problem_id": pid, "question_id": "a_q001", "stem_original": "صورت مسئله", "stem": "صورت مسئله",
            "review_status": "pending", "needs_review": True, "review_reasons": ["new_problem_class"],
            "source": {"exam_id": "a", "question_no": 1, "option_label": None, "pages": {"question": [3, 4], "answer": []}}}


class Fingerprint(unittest.TestCase):
    def test_stable_and_sensitive(self):
        s = stmt()
        self.assertEqual(fingerprint(s), fingerprint(copy.deepcopy(s)))
        t = stmt(text="متن دیگر")
        self.assertNotEqual(fingerprint(s), fingerprint(t))
        u = stmt()
        u["text_standalone"] = "بازنویسی LLM"          # text_standalone در fingerprint نیست
        self.assertEqual(fingerprint(s), fingerprint(u))
        v = stmt()
        v["source"]["option_label"] = "2"
        self.assertNotEqual(fingerprint(s), fingerprint(v))


class Queue(unittest.TestCase):
    def test_rows_and_csv_roundtrip(self):
        a = stmt("a_q001_o1", "گویچه‌ی قرمز، هسته «دارد»", True, reasons=["polarity_disagreement", "no_key"])
        b = stmt("a_q001_o2", "ok", True)
        c = stmt("a_q001_o3", "x", False, reasons=["no_key"], status="approved")
        p = prob()
        issues = [{"code": "missing_question_numbers", "exam_id": "a", "number": None, "pages": [], "detail": "[2]"}]
        rows = queue_rows([a, b, c], [p], issues)
        self.assertEqual([r["id"] for r in rows][:2], ["a_q001_o1", "a_q001_prob"])
        self.assertEqual(len(rows), 3)                    # b بدون پرچم و c تأییدشده نیستند
        self.assertEqual(rows[0]["reasons"], "polarity_disagreement|no_key")
        self.assertEqual((rows[0]["truth"], rows[0]["source_page"]), ("true", "q:1 a:2"))
        self.assertEqual(rows[1]["source_page"], "q:3-4")
        self.assertEqual(rows[2]["record_type"], "exam_issue")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "q.csv"
            write_queue_csv(path, rows)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))      # utf-8-sig برای Excel
            back = read_review_csv(path)
        self.assertEqual(list(back[0].keys()), QUEUE_COLUMNS)
        self.assertEqual(back[0]["text"], "گویچه‌ی قرمز، هسته «دارد»")   # ویرگول و نقل‌قول سالم
        self.assertEqual(len(back), 3)

    def test_excel_semicolon_and_tab_delimiters(self):
        for delim in (";", "\t"):
            txt = "\ufeff" + delim.join(["id", "decision", "truth_new"]) + "\r\n" + delim.join(["s1", "approved", "false"]) + "\r\n"
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "x.csv"
                p.write_bytes(txt.encode("utf-8"))
                self.assertEqual(read_review_csv(p), [{"id": "s1", "decision": "approved", "truth_new": "false"}])


class Parsing(unittest.TestCase):
    def test_truth_cells(self):
        for v, e in (("true", True), ("TRUE", True), ("۱", True), ("صحیح", True), ("درست", True), ("false", False),
                     ("0", False), ("۰", False), ("غلط", False), ("نادرست", False), ("null", None), ("نامشخص", None)):
            self.assertIs(parse_truth_cell(v), e, v)
        self.assertNotIn(parse_truth_cell(""), (True, False, None))   # UNSET
        with self.assertRaises(ValueError):
            parse_truth_cell("شاید")

    def test_build_edits(self):
        recs = {"s1": stmt("s1"), "s2": stmt("s2"), "s3": stmt("s3"), "p1": prob("p1")}
        rows = [{"id": "s1", "decision": "تأیید"}, {"id": "s2", "truth_new": "false", "note": "n"},
                {"id": "s3", "decision": "حذف", "truth_new": "true"}, {"id": "s4", "decision": "approved"},
                {"id": "exam:a:missing:-:0", "decision": "approved"}, {"id": "p1", "truth_new": "true"},
                {"id": "s1x", "decision": "approved"}, {"id": ""}, {"id": "s2", "decision": "شاید"},
                {"id": "s3", "decision": ""}]
        edits, errors, ignored = build_edits_from_rows(rows, recs)
        self.assertEqual(edits["s1"]["decision"], "approved")
        self.assertEqual((edits["s2"]["decision"], edits["s2"]["patch"], edits["s2"]["note"]), ("edited", {"truth": False}, "n"))
        self.assertEqual((edits["s3"]["decision"], edits["s3"]["patch"]), ("rejected", {}))
        self.assertEqual(ignored, 1)
        self.assertEqual(len(errors), 4)                    # s4، p1(truth)، s1x، decision نامعتبر
        self.assertTrue(all(e["fingerprint"] for e in edits.values()))


class Apply(unittest.TestCase):
    def edit(self, rec, decision, patch=None, at="2026-01-01T00:00:00+00:00", note=""):
        return {"id": rec["statement_id"], "record_type": "statement", "decision": decision, "patch": patch or {},
                "note": note, "fingerprint": fingerprint(rec), "at": at}

    def test_approve_edit_reject(self):
        a = stmt("a_q001_o1", truth=None, reasons=["no_key"])
        b = stmt("a_q001_o2", truth=True, reasons=["polarity_disagreement"])
        c = stmt("a_q001_o3", truth=True, reasons=["x"])
        d = stmt("a_q001_o4", truth=False, reasons=["y"])
        edits = {a["statement_id"]: self.edit(a, "edited", {"truth": True, "text_standalone": "متن اصلاح‌شده"}, note="طبق کتاب"),
                 b["statement_id"]: self.edit(b, "approved"), c["statement_id"]: self.edit(c, "rejected"),
                 d["statement_id"]: self.edit(d, "edited", {"truth": None})}
        stale = apply_edits([a, b, c, d], edits)
        self.assertEqual(stale, 0)
        self.assertEqual((a["truth"], a["truth_basis"], a["review_status"], a["needs_review"]), (True, "manual", "edited", False))
        self.assertEqual(a["text_standalone"], "متن اصلاح‌شده")
        self.assertEqual(a["review"]["previous_truth"], None)
        self.assertEqual((a["review"]["previous_basis"], a["review"]["note"]), ("unresolved", "طبق کتاب"))
        self.assertEqual((b["truth"], b["truth_basis"], b["review_status"], b["needs_review"]), (True, "key_derived", "approved", False))
        self.assertEqual((c["review_status"], c["needs_review"]), ("rejected", False))
        self.assertEqual((d["truth"], d["truth_basis"]), (None, "unresolved"))
        self.assertEqual(b["review_reasons"], ["polarity_disagreement"])       # تاریخچه‌ی پرچم می‌ماند

    def test_stale_edit_not_applied(self):
        a = stmt("a_q001_o1", truth=True, reasons=["r"])
        e = self.edit(a, "edited", {"truth": False})
        a["text_original"] = "متن بعد از استخراج دوباره عوض شد"
        self.assertEqual(apply_edits([a], {a["statement_id"]: e}), 1)
        self.assertEqual((a["truth"], a["review_status"]), (True, "pending"))
        self.assertIn(STALE, a["review_reasons"])

    def test_problem_approval(self):
        p = prob()
        e = {"id": p["problem_id"], "record_type": "problem", "decision": "approved", "patch": {}, "note": "",
             "fingerprint": fingerprint(p), "at": "t"}
        apply_edits([p], {p["problem_id"]: e})
        self.assertEqual((p["review_status"], p["needs_review"]), ("approved", False))

    def test_store_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_edits(d), {})
            old = {"s1": {"id": "s1", "at": "2026-01-01", "decision": "approved"},
                   "s2": {"id": "s2", "at": "2026-03-01", "decision": "approved"}}
            new = {"s1": {"id": "s1", "at": "2026-02-01", "decision": "rejected"},
                   "s2": {"id": "s2", "at": "2026-01-01", "decision": "rejected"},
                   "s3": {"id": "s3", "at": "2026-01-01", "decision": "approved"}}
            m = merge_edits(old, new)
            self.assertEqual((m["s1"]["decision"], m["s2"]["decision"], m["s3"]["decision"]), ("rejected", "approved", "approved"))
            save_edits(d, m)
            self.assertEqual(load_edits(d), m)


if __name__ == "__main__":
    unittest.main()
