# -*- coding: utf-8 -*-
"""مرحله ۵الف: validate. classified.jsonl + statements_raw.jsonl + problems_raw.jsonl ← validated/*.jsonl.

سه سطح:
  ۱) خطای schema/محتوا (نوع نادرست، فیلد الزامی غایب، متن خالی، truth بدون basis، یتیم‌بودن) ← رکورد رد می‌شود
     (rejected_invalid.jsonl) و هرگز به خروجی نمی‌رسد.
  ۲) قواعد کسب‌وکار (کلید نامنطبق با گزینه‌ها، سال/گروه نامشخص، مبحث خارج از taxonomy، مشکل‌های assemble)
     ← پرچم قاعده‌محور روی رکورد (needs_review)، نه رد.
  ۳) Pydantic (اگر نصب باشد) به‌عنوان بررسی دوم؛ اختلاف با اعتبارسنج خالص فقط گزارش می‌شود (pydantic_disagreement).
"""
from collections import Counter
from pathlib import Path

from ..records import Problem, Question, Statement, to_pydantic, validate
from ..taxonomy import topic_ids
from ..utils import read_json, read_jsonl, write_json, write_jsonl

# مشکل‌های assemble که روی درستیِ گزاره‌های همان سوال اثر دارند
PROPAGATED_ASSEMBLE_CODES = ("unusual_option_count", "duplicate_question_number", "key_not_in_options",
                             "key_conflict_between_sources", "stitch_number_mismatch", "dangling_continuation",
                             "orphan_continuation", "duplicate_option_labels")


class PydanticChecker:
    def __init__(self):
        self.available, self.error, self.models = False, None, {}
        try:
            import pydantic  # noqa: F401
            for name, cls in (("statement", Statement), ("problem", Problem), ("question", Question)):
                self.models[name] = to_pydantic(cls)
            self.available = True
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001  ساخت مدل خراب است؛ بدون آن ادامه می‌دهیم و صریح گزارش می‌کنیم
            self.error = f"{type(e).__name__}: {e}"

    def check(self, kind, rec):
        if not self.available:
            return []
        try:
            self.models[kind].model_validate(rec)
            return []
        except Exception as e:  # noqa: BLE001
            return [str(e).splitlines()[0] if str(e) else type(e).__name__]


def _issue_reasons(assemble_issues, questions):
    """-> {(exam_id, number): {reasons}}"""
    out = {}
    pages_of = [(q["exam"]["exam_id"], q["number"], set(q["pages"]["question"])) for q in questions]
    for iss in assemble_issues:
        if iss["code"] not in PROPAGATED_ASSEMBLE_CODES or iss.get("number") is None:
            continue
        reason = f"asm_{iss['code']}"
        if iss.get("exam_id"):
            out.setdefault((iss["exam_id"], iss["number"]), set()).add(reason)
        else:
            for ex, num, pg in pages_of:
                if num == iss["number"] and pg & set(iss.get("pages", [])):
                    out.setdefault((ex, num), set()).add(reason)
    return out


def question_flags(q, valid_topics):
    r = []
    labels = [o["label"] for o in q["options"]]
    status, correct = q["answer_status"], q["correct"]
    if status in ("official", "multiple_keys") and not correct:
        r.append("key_status_inconsistent")
    if correct and status in ("annulled", "disputed", "missing"):
        r.append("key_status_inconsistent")
    if status in ("official", "multiple_keys") and any(c not in labels for c in correct):
        r.append("key_not_in_options")
    if q["exam"].get("year_jalali") is None:
        r.append("source_missing_year")
    if not q["exam"].get("group"):
        r.append("source_missing_group")
    if valid_topics is not None and any(t not in valid_topics for t in q["topic_ids"]):
        r.append("topic_not_in_taxonomy")
    return r


def _content_errors_question(q):
    e = []
    if not q["stem_original"].strip():
        e.append("متن صورت سوال خالی است")
    if not q["pages"]["question"]:
        e.append("صفحه‌ی منبع سوال مشخص نیست")
    for o in q["options"]:
        if not o["text_original"].strip():
            e.append(f"متن گزینه‌ی {o['label']} خالی است")
    return e


def _content_errors_statement(s, qids):
    e = []
    if not s["text_original"].strip() or not s["text_standalone"].strip():
        e.append("متن گزاره خالی است")
    if (s["truth"] is None) != (s["truth_basis"] == "unresolved"):
        e.append(f"truth={s['truth']!r} با truth_basis={s['truth_basis']!r} سازگار نیست (هیچ truth بدون basis)")
    if s["truth_basis"] == "explanation" and not (s.get("truth_evidence") or "").strip():
        e.append("truth_basis=explanation بدون truth_evidence")
    if s["question_id"] not in qids:
        e.append(f"سوال والد {s['question_id']} در خروجی نیست")
    if not s["source"]["pages"]["question"]:
        e.append("صفحه‌ی منبع مشخص نیست")
    return e


def _content_errors_problem(p, qids):
    e = []
    if not p["stem_original"].strip():
        e.append("صورت مسئله خالی است")
    if p["question_id"] not in qids:
        e.append(f"سوال والد {p['question_id']} در خروجی نیست")
    return e


def validate_project(project_dir, taxonomy=None, log=print):
    pd = Path(project_dir)
    for f in ("classified.jsonl", "statements_raw.jsonl", "problems_raw.jsonl"):
        if not (pd / f).exists():
            raise RuntimeError(f"{f} نیست؛ ابتدا مرحله‌های قبلی (classify و atomize) را اجرا کنید.")
    questions = read_jsonl(pd / "classified.jsonl")
    statements = read_jsonl(pd / "statements_raw.jsonl")
    problems = read_jsonl(pd / "problems_raw.jsonl")
    asm = (read_json(pd / "assemble_report.json") or {}).get("issues", [])
    valid_topics = set(topic_ids(taxonomy)) if taxonomy else None
    pyd = PydanticChecker()
    rejected, error_log, flags = [], [], Counter()
    disagreements = []

    def reject(kind, rid, rec, errors):
        rejected.append({"record_type": kind, "id": rid, "errors": errors, "record": rec})
        error_log.append({"record_type": kind, "id": rid, "errors": errors})

    # --- سوال‌ها
    good_q, seen = [], set()
    for q in questions:
        rid = q.get("question_id", "?")
        errs = validate(Question, q)
        if not errs:
            errs = _content_errors_question(q)
            if rid in seen:
                errs.append("question_id تکراری")
        if errs:
            reject("question", rid, q, errs)
            continue
        seen.add(rid)
        extra = pyd.check("question", q)
        if extra:
            disagreements.append({"id": rid, "pydantic": extra})
        reasons = set(q["review_reasons"]) | set(question_flags(q, valid_topics))
        q["review_reasons"] = sorted(reasons)
        good_q.append(q)
    qids = {q["question_id"] for q in good_q}
    asm_reasons = _issue_reasons(asm, good_q)
    q_reasons = {q["question_id"]: set(q["review_reasons"]) for q in good_q}
    for q in good_q:
        q["review_reasons"] = sorted(set(q["review_reasons"]) | asm_reasons.get((q["exam"]["exam_id"], q["number"]), set()))
        q_reasons[q["question_id"]] = set(q["review_reasons"])

    def propagate(rec):
        """پرچم‌های سطح سوال که روی درستیِ این رکورد اثر دارند + پرچم‌های منبع."""
        qid = rec["question_id"]
        extra = {r for r in q_reasons.get(qid, set())
                 if r.startswith("asm_") or r in ("key_status_inconsistent", "key_not_in_options", "source_missing_year",
                                                  "source_missing_group", "topic_not_in_taxonomy")}
        rec["review_reasons"] = sorted(set(rec["review_reasons"]) | extra)
        rec["needs_review"] = bool(rec["review_reasons"]) if rec["review_status"] == "pending" else False
        for r in extra:
            flags[r] += 1

    # --- گزاره‌ها
    good_s, seen_s = [], set()
    for s in statements:
        rid = s.get("statement_id", "?")
        errs = validate(Statement, s)
        if not errs:
            errs = _content_errors_statement(s, qids)
            if rid in seen_s:
                errs.append("statement_id تکراری")
        if errs:
            reject("statement", rid, s, errs)
            continue
        seen_s.add(rid)
        extra = pyd.check("statement", s)
        if extra:
            disagreements.append({"id": rid, "pydantic": extra})
        propagate(s)
        good_s.append(s)

    # --- مسئله‌ها
    good_p, seen_p = [], set()
    for p in problems:
        rid = p.get("problem_id", "?")
        errs = validate(Problem, p)
        if not errs:
            errs = _content_errors_problem(p, qids)
            if rid in seen_p:
                errs.append("problem_id تکراری")
        if errs:
            reject("problem", rid, p, errs)
            continue
        seen_p.add(rid)
        extra = pyd.check("problem", p)
        if extra:
            disagreements.append({"id": rid, "pydantic": extra})
        propagate(p)
        good_p.append(p)

    out = pd / "validated"
    write_jsonl(out / "questions.jsonl", good_q)
    write_jsonl(out / "statements.jsonl", good_s)
    write_jsonl(out / "problems.jsonl", good_p)
    write_jsonl(pd / "rejected_invalid.jsonl", rejected)
    report = {
        "questions": {"in": len(questions), "valid": len(good_q)},
        "statements": {"in": len(statements), "valid": len(good_s)},
        "problems": {"in": len(problems), "valid": len(good_p)},
        "rejected": len(rejected), "errors": error_log[:100], "flag_counts": dict(flags),
        "pydantic": {"available": pyd.available, "model_build_error": pyd.error,
                     "disagreements": len(disagreements), "examples": disagreements[:5]},
        "taxonomy_checked": valid_topics is not None}
    write_json(pd / "validation_report.json", report)
    if pyd.error:
        log(f"[WARN] مدل‌های Pydantic ساخته نشدند ({pyd.error}); فقط اعتبارسنج خالص اجرا شد.")
    if disagreements:
        log(f"[WARN] {len(disagreements)} رکورد را اعتبارسنج خالص پذیرفت ولی Pydantic نه (نمونه‌ها در validation_report.json).")
    if any(q["exam"].get("year_jalali") is None for q in good_q):
        log("[WARN] سال شمسی آزمون مشخص نیست (--year یا manifest)؛ رکوردها پرچم source_missing_year می‌گیرند.")
    return report
