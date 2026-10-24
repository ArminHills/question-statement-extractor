# -*- coding: utf-8 -*-
"""صف بازبینی انسانی. ویرایش‌ها در review_edits.jsonl (جدا از خروجی مراحل) نگه‌داری و در هر export دوباره اعمال
می‌شوند؛ پس اجرای دوباره‌ی مراحل هرگز ویرایش دستی را بازنویسی نمی‌کند.

fingerprint رکورد (متن اصلی + شناسه‌ی سوال + برچسب گزینه): اگر متن رکورد بعد از بازبینی عوض شده باشد
(مثلاً استخراج دوباره)، ویرایش کهنه اعمال نمی‌شود و پرچم stale_review_edit می‌خورد.
فقط حکم بازبین (truth، متن مستقل) و تصمیم (approved/edited/rejected) اعمال می‌شود؛ پرچم‌های قاعده‌محور به‌عنوان تاریخچه می‌مانند."""
import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

from .normalize import normalize_text
from .utils import atomic_write_text, read_jsonl, write_jsonl

EDITS_FILE = "review_edits.jsonl"
STALE = "stale_review_edit"
QUEUE_COLUMNS = ["id", "record_type", "reasons", "text", "truth", "source_page", "exam_id", "question_no",
                 "option_label", "review_status", "decision", "truth_new", "text_new", "note"]
_UNSET = object()


def record_type(rec):
    return "statement" if "statement_id" in rec else "problem"


def record_id(rec):
    return rec["statement_id"] if "statement_id" in rec else rec["problem_id"]


def fingerprint(rec):
    if "statement_id" in rec:
        raw = "\0".join([rec["text_original"], rec["question_id"], str(rec["source"].get("option_label")),
                         rec["statement_id"]])
    else:
        raw = "\0".join([rec["stem_original"], rec["question_id"], rec["problem_id"]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- ذخیره‌ی ویرایش‌ها
def load_edits(project_dir):
    p = Path(project_dir) / EDITS_FILE
    if not p.exists():
        return {}
    return {e["id"]: e for e in read_jsonl(p)}


def save_edits(project_dir, edits):
    write_jsonl(Path(project_dir) / EDITS_FILE, [edits[k] for k in sorted(edits)])


def merge_edits(a, b):
    """اتحاد دو مجموعه ویرایش؛ برای هر id ویرایش تازه‌تر (at) برنده است."""
    out = dict(a)
    for k, e in b.items():
        if k not in out or (e.get("at") or "") >= (out[k].get("at") or ""):
            out[k] = e
    return out


# --------------------------------------------------------------------- اعمال
def apply_edits(records, edits):
    """روی لیست رکوردها (درجا). -> تعداد ویرایش کهنه."""
    stale = 0
    for rec in records:
        e = edits.get(record_id(rec))
        if not e:
            continue
        if e["fingerprint"] != fingerprint(rec):
            stale += 1
            rec.setdefault("review_reasons", [])
            if STALE not in rec["review_reasons"]:
                rec["review_reasons"].append(STALE)
            continue
        prev = {"previous_truth": rec.get("truth"), "previous_basis": rec.get("truth_basis")} \
            if record_type(rec) == "statement" else {}
        patch = e.get("patch") or {}
        decision = e["decision"]
        if decision == "rejected":
            rec["review_status"] = "rejected"
        else:
            if record_type(rec) == "statement":
                if "truth" in patch:
                    rec["truth"] = patch["truth"]
                    rec["truth_basis"] = "manual" if patch["truth"] is not None else "unresolved"
                if patch.get("text_standalone"):
                    rec["text_standalone"] = patch["text_standalone"]
            rec["review_status"] = "edited" if patch else "approved"
        rec["needs_review"] = False
        rec["review"] = {"at": e.get("at"), "note": e.get("note") or None, **prev}
    return stale


# ------------------------------------------------------------------ CSV صادرات
def _pages(p):
    def rng(xs):
        return "-".join(map(str, (xs[0], xs[-1]))) if len(xs) > 1 and xs[-1] != xs[0] else (str(xs[0]) if xs else "")
    q, a = rng(p.get("question", [])), rng(p.get("answer", []))
    return f"q:{q}" + (f" a:{a}" if a else "")


def queue_rows(statements, problems, exam_issues=()):
    rows = []
    for rec in list(statements) + list(problems):
        if rec["review_status"] != "pending" or not rec["needs_review"]:
            continue
        s = rec["source"]
        is_st = record_type(rec) == "statement"
        rows.append({
            "id": record_id(rec), "record_type": record_type(rec), "reasons": "|".join(rec["review_reasons"]),
            "text": rec["text_standalone"] if is_st else rec["stem"],
            "truth": "" if not is_st or rec["truth"] is None else ("true" if rec["truth"] else "false"),
            "source_page": _pages(s["pages"]), "exam_id": s["exam_id"], "question_no": s["question_no"],
            "option_label": s.get("option_label") or "", "review_status": rec["review_status"],
            "decision": "", "truth_new": "", "text_new": "", "note": ""})
    for i, iss in enumerate(exam_issues):
        rows.append({
            "id": f"exam:{iss.get('exam_id') or '-'}:{iss['code']}:{iss.get('number') or '-'}:{i}",
            "record_type": "exam_issue", "reasons": iss["code"], "text": iss.get("detail") or "", "truth": "",
            "source_page": ("q:" + ",".join(map(str, iss.get("pages", [])))) if iss.get("pages") else "",
            "exam_id": iss.get("exam_id") or "", "question_no": iss.get("number") or "", "option_label": "",
            "review_status": "info", "decision": "", "truth_new": "", "text_new": "", "note": ""})
    return rows


def write_queue_csv(path, rows):
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=QUEUE_COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    # utf-8-sig تا Excel فارسی را درست نشان دهد
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(("\ufeff" + buf.getvalue()).encode("utf-8"))


# ---------------------------------------------------------------- CSV ورودی
_DECISIONS = {"approved": "approved", "approve": "approved", "ok": "approved", "yes": "approved", "تایید": "approved",
              "تأیید": "approved", "edited": "edited", "edit": "edited", "ویرایش": "edited",
              "rejected": "rejected", "reject": "rejected", "delete": "rejected", "حذف": "rejected", "رد": "rejected"}
_TRUE = {"true", "1", "t", "صحیح", "درست"}
_FALSE = {"false", "0", "f", "غلط", "نادرست"}
_NULL = {"null", "none", "نامشخص"}


def parse_truth_cell(v):
    t = normalize_text(v or "").strip().lower()
    if not t:
        return _UNSET
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    if t in _NULL:
        return None
    raise ValueError(f"مقدار truth_new نامعتبر: {v!r} (true/false/null یا صحیح/غلط)")


def read_review_csv(path):
    raw = Path(path).read_bytes().decode("utf-8-sig")
    first = raw.splitlines()[0] if raw else ""
    delim = max((",", ";", "\t"), key=first.count)
    return list(csv.DictReader(io.StringIO(raw, newline=""), delimiter=delim))


def build_edits_from_rows(rows, records_by_id):
    """-> (edits dict, errors list, ignored count)."""
    edits, errors, ignored = {}, [], 0
    for n, row in enumerate(rows, start=2):  # ردیف ۱ = سرستون
        rid = (row.get("id") or "").strip()
        if not rid:
            continue
        if rid.startswith("exam:"):
            ignored += 1
            continue
        rec = records_by_id.get(rid)
        if rec is None:
            errors.append(f"ردیف {n}: شناسه‌ی ناشناخته {rid!r}")
            continue
        raw_dec = normalize_text(row.get("decision") or "").strip().lower()
        if raw_dec and raw_dec not in _DECISIONS:
            errors.append(f"ردیف {n} ({rid}): decision نامعتبر {row.get('decision')!r}")
            continue
        try:
            truth = parse_truth_cell(row.get("truth_new"))
        except ValueError as e:
            errors.append(f"ردیف {n} ({rid}): {e}")
            continue
        patch = {}
        if truth is not _UNSET:
            if record_type(rec) != "statement":
                errors.append(f"ردیف {n} ({rid}): truth_new فقط برای گزاره معنا دارد")
                continue
            patch["truth"] = truth
        text_new = (row.get("text_new") or "").strip()
        if text_new:
            if record_type(rec) != "statement":
                errors.append(f"ردیف {n} ({rid}): text_new فقط برای گزاره معنا دارد")
                continue
            patch["text_standalone"] = text_new
        decision = _DECISIONS.get(raw_dec)
        if decision is None and not patch:
            continue  # ردیف دست‌نخورده
        if decision == "rejected":
            patch = {}
        elif patch:
            decision = "edited"
        edits[rid] = {"id": rid, "record_type": record_type(rec), "decision": decision, "patch": patch,
                      "note": (row.get("note") or "").strip(), "fingerprint": fingerprint(rec), "at": now_iso()}
    return edits, errors, ignored
