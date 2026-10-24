# -*- coding: utf-8 -*-
"""مرحله ۵ب: export. validated/*.jsonl + review_edits.jsonl ← خروجی‌های نهایی.

ترتیب: اعمال ویرایش‌های بازبین → dedupe و تعارض truth (روی truth ویرایش‌شده) → اعتبارسنجی نهایی → نوشتن.
validated/ هیچ‌وقت تغییر نمی‌کند، پس export را می‌توان هر چند بار (مثلاً بعد از import-review) اجرا کرد."""
import csv
import io
from pathlib import Path

from ..dedupe import dedupe_problems, dedupe_statements
from ..records import Problem, Question, Statement, validate
from ..review import apply_edits, load_edits, queue_rows, write_queue_csv
from ..schema_md import write_schema_files
from ..sqlite_out import write_sqlite
from ..utils import read_json, read_jsonl, write_json, write_jsonl

CSV_COLUMNS = ["statement_id", "question_id", "exam_id", "year_jalali", "group", "question_no", "option_label",
               "text_original", "text_standalone", "truth", "truth_basis", "why_false", "is_atomic", "standalone",
               "context_dependency", "context_ref", "topic_ids", "question_type", "structure", "polarity",
               "canonical_id", "occurrences", "needs_review", "review_reasons", "review_status", "llm_confidence",
               "pages_question", "pages_answer", "parent_statement_id"]


def statements_csv_rows(statements):
    for s in statements:
        src = s["source"]
        yield {
            "statement_id": s["statement_id"], "question_id": s["question_id"], "exam_id": src["exam_id"],
            "year_jalali": src.get("year_jalali") if src.get("year_jalali") is not None else "",
            "group": src.get("group") or "", "question_no": src["question_no"], "option_label": src.get("option_label") or "",
            "text_original": s["text_original"], "text_standalone": s["text_standalone"],
            "truth": "" if s["truth"] is None else ("true" if s["truth"] else "false"), "truth_basis": s["truth_basis"],
            "why_false": s.get("why_false") or "", "is_atomic": str(s["is_atomic"]).lower(),
            "standalone": str(s["standalone"]).lower(), "context_dependency": s["context_dependency"],
            "context_ref": s.get("context_ref") or "", "topic_ids": "|".join(s["topic_ids"]),
            "question_type": s["question_type"], "structure": s["structure"], "polarity": s.get("polarity") or "",
            "canonical_id": s["dedupe"]["canonical_id"], "occurrences": s["dedupe"]["occurrences"],
            "needs_review": str(s["needs_review"]).lower(), "review_reasons": "|".join(s["review_reasons"]),
            "review_status": s["review_status"], "llm_confidence": s["llm_confidence"],
            "pages_question": "|".join(map(str, src["pages"]["question"])),
            "pages_answer": "|".join(map(str, src["pages"]["answer"])),
            "parent_statement_id": s.get("parent_statement_id") or ""}


def write_statements_csv(path, statements):
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(statements_csv_rows(statements))
    Path(path).write_bytes(("\ufeff" + buf.getvalue()).encode("utf-8"))


def export_project(project_dir, *, similarity_threshold=90, sqlite=False, log=print):
    pd = Path(project_dir)
    base = pd / "validated"
    for f in ("questions", "statements", "problems"):
        if not (base / f"{f}.jsonl").exists():
            raise RuntimeError(f"validated/{f}.jsonl نیست؛ ابتدا validate را اجرا کنید.")
    questions = read_jsonl(base / "questions.jsonl")
    statements = read_jsonl(base / "statements.jsonl")
    problems = read_jsonl(base / "problems.jsonl")
    edits = load_edits(pd)
    stale = apply_edits(statements, edits) + apply_edits(problems, edits)
    st_stats = dedupe_statements(statements, similarity_threshold)
    pr_stats = dedupe_problems(problems, similarity_threshold)
    for s in statements:      # ویرایش کهنه پرچم داده شده؛ وضعیت needs_review را با پرچم‌ها هماهنگ نگه دار
        if s["review_status"] == "pending":
            s["needs_review"] = bool(s["review_reasons"])
    for p in problems:
        if p["review_status"] == "pending":
            p["review_reasons"] = sorted(set(p["review_reasons"]))
            p["needs_review"] = bool(p["review_reasons"])

    rejected_export, live_s, live_p, rejected_by_review = [], [], [], []
    for kind, cls, recs, live in (("statement", Statement, statements, live_s), ("problem", Problem, problems, live_p)):
        for r in recs:
            errs = validate(cls, r)
            if errs:
                rejected_export.append({"record_type": kind, "errors": errs, "record": r})
            elif r["review_status"] == "rejected":
                rejected_by_review.append(r)
            else:
                live.append(r)
    for q in questions:
        errs = validate(Question, q)
        if errs:
            rejected_export.append({"record_type": "question", "errors": errs, "record": q})

    exam_issues = (read_json(pd / "assemble_report.json") or {}).get("issues", [])
    write_jsonl(pd / "questions.jsonl", questions)
    write_jsonl(pd / "statements.jsonl", live_s)
    write_jsonl(pd / "problems.jsonl", live_p)
    write_jsonl(pd / "rejected_by_review.jsonl", rejected_by_review)
    write_jsonl(pd / "rejected_export.jsonl", rejected_export)
    write_statements_csv(pd / "statements.csv", live_s)
    rows = queue_rows(live_s, live_p, exam_issues)
    write_queue_csv(pd / "review_queue.csv", rows)
    write_schema_files(pd)
    if sqlite:
        write_sqlite(pd / "bank.sqlite", questions, live_s, live_p)
    report = {
        "questions": len(questions), "statements": len(live_s), "problems": len(live_p),
        "statements_truth_known": sum(1 for s in live_s if s["truth"] is not None),
        "statements_needs_review": sum(1 for s in live_s if s["needs_review"]),
        "problems_needs_review": sum(1 for p in live_p if p["needs_review"]),
        "review_queue_rows": len(rows), "rejected_by_review": len(rejected_by_review),
        "rejected_export": len(rejected_export), "stale_review_edits": stale,
        "dedupe": {**st_stats, **pr_stats}, "sqlite": bool(sqlite)}
    write_json(pd / "export_report.json", report)
    log(f"[INFO] export: {report['questions']} سوال، {report['statements']} گزاره ({report['statements_truth_known']} با truth معلوم، "
        f"{report['statements_needs_review']} نیازمند بازبینی)، {report['problems']} مسئله؛ "
        f"{st_stats['truth_conflict_clusters']} خوشه‌ی تعارض truth؛ {stale} ویرایش کهنه.")
    return report
