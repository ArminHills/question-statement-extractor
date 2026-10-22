# -*- coding: utf-8 -*-
"""مرحله‌ی assemble (بدون LLM): سوارکردن آیتم‌های بریده‌شده، تخصیص آزمون، اتصال سوال-پاسخ، گزارش مشکل.

ورودی: رکورد صفحه‌ها (خروجی extract)، هر کدام:
  {"pdf": "name", "page": 41, "page_role": "...",
   "questions": [{"number": 12|None, "stem": "...", "options": [{"label","text","is_continuation"}],
                  "continues_from_previous_page": bool, "continues_on_next_page": bool}],
   "answers":   [{"number": 12|None, "correct": ["2"], "status": "official|annulled|disputed|None",
                  "explanation": "..."|None, "continues_from_previous_page": bool, "continues_on_next_page": bool}]}
  page_role برای answers تعیین می‌کند منبع «جدول کلید» است یا «پاسخ تشریحی».

قرارداد سوارکردن:
  • قطعه‌های stem با یک فاصله به هم وصل می‌شوند (متن اصلی دست‌نخورده می‌ماند، فقط الحاق).
  • اگر قطعه‌ی ادامه، متن قبل از اولین گزینه داشته باشد و سوالِ در حال انتظار گزینه دارد،
    آن متن به انتهای آخرین گزینه الحاق می‌شود؛ وگرنه به stem.
  • گزینه‌ی ادامه (is_continuation) به گزینه‌ی هم‌برچسب (یا آخرین گزینه) الحاق می‌شود.
  • اتصال فقط بین صفحه‌های متوالیِ یک PDF انجام می‌شود.
"""
import copy
from typing import Dict, List, Optional

from .normalize import normalize_label
from .utils import parse_pages_input


def _issue(code, exam_id=None, number=None, pages=None, detail=""):
    return {"code": code, "exam_id": exam_id, "number": number, "pages": pages or [], "detail": detail}


# ----------------------------------------------------------------- stitching
def _join(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    return f"{a} {b}".strip() if a and b else (a or b)


def _merge_question(pending, cont, issues):
    stem_part = cont.get("stem") or ""
    if stem_part.strip():
        if pending["options"]:
            pending["options"][-1]["text"] = _join(pending["options"][-1]["text"], stem_part)
        else:
            pending["stem_original"] = _join(pending["stem_original"], stem_part)
    for opt in cont.get("options", []):
        label = normalize_label(opt.get("label"))
        text = opt.get("text") or ""
        if opt.get("is_continuation"):
            target = next((o for o in pending["options"] if o["label"] == label), None)
            if target is None and pending["options"]:
                target = pending["options"][-1]
            if target is not None:
                target["text"] = _join(target["text"], text)
                continue
            issues.append(_issue("continuation_option_orphan", number=pending["number"],
                                 pages=pending["pages"], detail=f"label={label}"))
        pending["options"].append({"label": label, "text": text})


def _new_question(item, pdf, page):
    return {"number": item.get("number"), "stem_original": item.get("stem") or "",
            "options": [{"label": normalize_label(o.get("label")), "text": o.get("text") or ""}
                        for o in item.get("options", [])],
            "pages": [page], "pdf": pdf, "page_role": None}


def _new_answer(item, pdf, page, role):
    return {"number": item.get("number"), "correct": [normalize_label(c) for c in item.get("correct") or []],
            "status": item.get("status"), "explanation_original": item.get("explanation") or None,
            "pages": [page], "pdf": pdf, "role": role}


def _merge_answer(pending, cont):
    if cont.get("explanation"):
        pending["explanation_original"] = _join(pending["explanation_original"], cont["explanation"])
    for c in cont.get("correct") or []:
        c = normalize_label(c)
        if c not in pending["correct"]:
            pending["correct"].append(c)
    pending["status"] = pending["status"] or cont.get("status")


def _stitch(pages, key, new, merge, issues, kind):
    out, pending = [], None
    for rec in pages:
        for idx, item in enumerate(rec.get(key, [])):
            page, pdf = rec["page"], rec["pdf"]
            is_cont = bool(item.get("continues_from_previous_page")) and idx == 0
            if is_cont:
                ok = (pending is not None and pending["pdf"] == pdf and pending["pages"][-1] + 1 == page)
                if ok and item.get("number") not in (None, pending["number"]) and pending["number"] is not None:
                    issues.append(_issue("stitch_number_mismatch", number=pending["number"], pages=[page],
                                         detail=f"{kind}: {pending['number']} vs {item.get('number')}"))
                    ok = False
                if ok:
                    merge(pending, item)
                    pending["pages"].append(page)
                    if pending["number"] is None:
                        pending["number"] = item.get("number")
                    if not item.get("continues_on_next_page"):
                        out.append(pending)
                        pending = None
                    continue
                if pending is not None:  # ادامه‌ی ناسازگار: سوال معلق را رها می‌کنیم
                    issues.append(_issue("dangling_continuation", number=pending["number"], pages=pending["pages"],
                                         detail=kind))
                    out.append(pending)
                    pending = None
                if pending is None and not ok:
                    issues.append(_issue("orphan_continuation", number=item.get("number"), pages=[page], detail=kind))
            elif pending is not None:
                issues.append(_issue("dangling_continuation", number=pending["number"], pages=pending["pages"],
                                     detail=kind))
                out.append(pending)
                pending = None
            cur = new(item, pdf, page, rec.get("page_role")) if kind == "answer" else new(item, pdf, page)
            if kind == "question":
                cur["page_role"] = rec.get("page_role")
            if item.get("continues_on_next_page"):
                pending = cur
            else:
                out.append(cur)
    if pending is not None:
        issues.append(_issue("dangling_continuation", number=pending["number"], pages=pending["pages"],
                             detail=f"{kind}: ادامه در صفحه‌ی بعد پیدا نشد"))
        out.append(pending)
    return out


def stitch_pages(pages):
    """-> (questions, answers, issues). pages باید به ترتیب (pdf, page) باشند؛ این‌جا مرتب می‌شود."""
    pages = sorted(copy.deepcopy(pages), key=lambda r: (r["pdf"], r["page"]))
    issues: List[dict] = []
    questions = _stitch(pages, "questions", _new_question, _merge_question_wrapper(issues), issues, "question")
    answers = _stitch(pages, "answers", _new_answer, lambda p, c: _merge_answer(p, c), issues, "answer")
    return questions, answers, issues


def _merge_question_wrapper(issues):
    return lambda p, c: _merge_question(p, c, issues)


# ------------------------------------------------------------------ manifest
class Manifest:
    """هر ورودی: exam_id, year_jalali, group, session, textbook_edition, question_pages, answer_pages,
    question_pdf (اختیاری)، answer_pdf (اختیاری). pdf پیش‌فرض: pdf اصلی."""
    def __init__(self, entries: List[dict], default_pdf: Optional[str] = None):
        self.entries = []
        for e in entries:
            e = dict(e)
            e["_qpages"] = set(parse_pages_input(str(e["question_pages"]))) if e.get("question_pages") else set()
            e["_apages"] = set(parse_pages_input(str(e["answer_pages"]))) if e.get("answer_pages") else set()
            e["_qpdf"] = e.get("question_pdf") or default_pdf
            e["_apdf"] = e.get("answer_pdf") or e.get("question_pdf") or default_pdf
            self.entries.append(e)

    @classmethod
    def from_yaml(cls, path, default_pdf=None):
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data["exams"] if isinstance(data, dict) else data, default_pdf)

    @classmethod
    def single(cls, exam_id, year_jalali=None, group=None, default_pdf=None, session=None, edition=None):
        return cls([{"exam_id": exam_id, "year_jalali": year_jalali, "group": group, "session": session,
                     "textbook_edition": edition, "question_pages": None, "answer_pages": None}], default_pdf)

    def find(self, pdf, page, want):
        """want: 'q' | 'a'. ورودی تک‌آزمونه بدون محدوده همه‌ی صفحه‌ها را می‌گیرد."""
        for e in self.entries:
            if e["_qpages"] or e["_apages"]:
                if want == "q" and page in e["_qpages"] and e["_qpdf"] in (None, pdf):
                    return e
                if want == "a" and (page in e["_apages"] and e["_apdf"] in (None, pdf)
                                    or page in e["_qpages"] and e["_qpdf"] in (None, pdf)):
                    return e
            elif len(self.entries) == 1:
                return e
        return None


def exam_meta(e):
    return {"exam_id": e["exam_id"], "year_jalali": e.get("year_jalali"), "group": e.get("group"),
            "session": e.get("session"), "textbook_edition": e.get("textbook_edition")}


# ------------------------------------------------------------------- linking
def _status(ans):
    st = ans.get("status")
    if st in ("annulled", "disputed"):
        return st
    n = len(ans["correct"])
    if n > 1:
        return "multiple_keys"
    if n == 1:
        return "official"
    return "missing"


def link_and_check(questions, answers, manifest: Manifest, layout="auto", expected_option_counts=(4,),
                   stitch_issues=None):
    """-> (question_records, issues). رکوردها هنوز classify نشده‌اند."""
    issues = list(stitch_issues or [])
    by_exam_q: Dict[str, List[dict]] = {}
    exams: Dict[str, dict] = {}
    for q in questions:
        e = manifest.find(q["pdf"], q["pages"][0], "q")
        if e is None:
            issues.append(_issue("question_outside_manifest", number=q["number"], pages=q["pages"]))
            continue
        exams[e["exam_id"]] = e
        by_exam_q.setdefault(e["exam_id"], []).append(q)

    answer_map: Dict[tuple, List[dict]] = {}
    for a in answers:
        e = manifest.find(a["pdf"], a["pages"][0], "a")
        if e is None:
            issues.append(_issue("answer_outside_manifest", number=a["number"], pages=a["pages"]))
            continue
        exams[e["exam_id"]] = e
        if layout == "interleaved" and not (a["pages"][0] in e["_qpages"] or not e["_qpages"]):
            issues.append(_issue("unexpected_answer_location", e["exam_id"], a["number"], a["pages"],
                                 "layout=interleaved ولی پاسخ خارج از صفحه‌های سوال است"))
        if layout == "separate" and e["_apages"] and a["pages"][0] not in e["_apages"]:
            issues.append(_issue("unexpected_answer_location", e["exam_id"], a["number"], a["pages"],
                                 "layout=separate ولی پاسخ در صفحه‌های پاسخ نیست"))
        if a["number"] is None:
            issues.append(_issue("answer_without_number", e["exam_id"], None, a["pages"]))
            continue
        answer_map.setdefault((e["exam_id"], a["number"]), []).append(a)

    records = []
    used_answers = set()
    for exam_id, qs in by_exam_q.items():
        meta = exam_meta(exams[exam_id])
        seen = {}
        for q in qs:
            if q["number"] is None:
                issues.append(_issue("question_without_number", exam_id, None, q["pages"]))
                continue
            seen.setdefault(q["number"], []).append(q)
        for n, group in seen.items():
            if len(group) > 1:
                issues.append(_issue("duplicate_question_number", exam_id, n, [p for g in group for p in g["pages"]]))
        nums = sorted(seen)
        if nums:
            missing = [n for n in range(nums[0], nums[-1] + 1) if n not in seen]
            if missing:
                issues.append(_issue("missing_question_numbers", exam_id, None, [], f"{missing}"))
        for q in qs:
            if q["number"] is None:
                continue
            cands = answer_map.get((exam_id, q["number"]), [])
            used_answers.add((exam_id, q["number"]))
            key_a = [a for a in cands if a["role"] == "answer_key"]
            exp_a = [a for a in cands if a["role"] != "answer_key"]
            if len(cands) > len({tuple(a["pages"]) for a in cands}) and len(key_a) > 1:
                issues.append(_issue("duplicate_answer", exam_id, q["number"]))
            primary = (key_a or cands or [None])[0]
            correct, status = [], "missing"
            source, explanation, apages = "none", None, []
            if primary is not None:
                correct = list(primary["correct"])
                status = _status(primary)
                apages = list(primary["pages"])
            for a in exp_a:
                if a["explanation_original"] and explanation is None:
                    explanation = a["explanation_original"]
                    apages = sorted(set(apages + a["pages"]))
                if not correct and a["correct"]:
                    correct, status = list(a["correct"]), _status(a)
                elif correct and a["correct"] and set(a["correct"]) != set(correct):
                    issues.append(_issue("key_conflict_between_sources", exam_id, q["number"], a["pages"],
                                         f"{correct} vs {a['correct']}"))
                if a["status"] in ("annulled", "disputed") and status not in ("annulled", "disputed"):
                    status = a["status"]
            if cands:
                source = "both" if key_a and exp_a else ("answer_key" if key_a else "explanation")
            labels = [o["label"] for o in q["options"]]
            if not q["options"] or len(labels) not in expected_option_counts:
                issues.append(_issue("unusual_option_count", exam_id, q["number"], q["pages"], f"{len(labels)}"))
            if len(set(labels)) != len(labels):
                issues.append(_issue("duplicate_option_labels", exam_id, q["number"], q["pages"], f"{labels}"))
            if status in ("official", "multiple_keys") and any(c not in labels for c in correct):
                issues.append(_issue("key_not_in_options", exam_id, q["number"], q["pages"], f"{correct} vs {labels}"))
            if status == "missing":
                issues.append(_issue("question_without_answer", exam_id, q["number"], q["pages"]))
            records.append({
                "exam": meta, "number": q["number"], "stem_original": q["stem_original"],
                "options": q["options"], "correct": correct, "answer_status": status,
                "answer_source": source, "explanation_original": explanation,
                "pages": {"question": q["pages"], "answer": apages}, "pdf": q["pdf"],
            })
    for (exam_id, n) in answer_map:
        if (exam_id, n) not in used_answers:
            issues.append(_issue("answer_without_question", exam_id, n))
    records.sort(key=lambda r: (r["exam"]["exam_id"], r["number"]))
    return records, issues
