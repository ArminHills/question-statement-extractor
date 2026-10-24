# -*- coding: utf-8 -*-
"""bank.sqlite (اختیاری): هر رکورد یک ردیف با ستون‌های کلیدی برای فیلتر سریع + JSON کامل."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

SCHEMA = """
CREATE TABLE questions (question_id TEXT PRIMARY KEY, exam_id TEXT, year_jalali INTEGER, number INTEGER,
    structure TEXT, route TEXT, answer_status TEXT, json TEXT NOT NULL);
CREATE TABLE statements (statement_id TEXT PRIMARY KEY, question_id TEXT, truth INTEGER, truth_basis TEXT,
    structure TEXT, question_type TEXT, topic_main TEXT, canonical_id TEXT, occurrences INTEGER, standalone INTEGER,
    needs_review INTEGER, review_status TEXT, text_standalone TEXT, json TEXT NOT NULL);
CREATE TABLE problems (problem_id TEXT PRIMARY KEY, question_id TEXT, problem_class TEXT, topic_main TEXT,
    needs_review INTEGER, review_status TEXT, json TEXT NOT NULL);
CREATE INDEX ix_st_q ON statements(question_id);
CREATE INDEX ix_st_canon ON statements(canonical_id);
CREATE INDEX ix_st_truth ON statements(truth, needs_review);
CREATE INDEX ix_st_topic ON statements(topic_main);
"""


def _j(r):
    return json.dumps(r, ensure_ascii=False, sort_keys=True)


def write_sqlite(path, questions, statements, problems):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".sqlite.tmp")
    os.close(fd)
    os.unlink(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?)", [
            (q["question_id"], q["exam"]["exam_id"], q["exam"].get("year_jalali"), q["number"], q["structure"],
             q["route"], q["answer_status"], _j(q)) for q in questions])
        con.executemany("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (s["statement_id"], s["question_id"], None if s["truth"] is None else int(s["truth"]), s["truth_basis"],
             s["structure"], s["question_type"], (s["topic_ids"] or [None])[0], s["dedupe"]["canonical_id"],
             s["dedupe"]["occurrences"], int(s["standalone"]), int(s["needs_review"]), s["review_status"],
             s["text_standalone"], _j(s)) for s in statements])
        con.executemany("INSERT INTO problems VALUES (?,?,?,?,?,?,?)", [
            (p["problem_id"], p["question_id"], p["problem_class"], (p["topic_ids"] or [None])[0],
             int(p["needs_review"]), p["review_status"], _j(p)) for p in problems])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)
