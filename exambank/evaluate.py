# -*- coding: utf-8 -*-
"""evaluate: مقایسه‌ی خروجی با داده‌ی طلایی شما (gold/*.jsonl با همان schema).

فایل‌های gold (هر خط یک رکورد؛ فقط فیلدهایی که می‌خواهید سنجیده شوند لازم است):
  • رکورد سوال: {"question_id": "...", "correct": ["3"], "answer_status": "official", "structure": "...",
                 "polarity": "negative"|"positive"|null, "route": "...", "question_type": "...", "topic_ids": ["..."]}
  • رکورد گزاره: {"statement_id": "...", "truth": true|false|null, "structure": "..."}
شناسه‌ها باید همان شناسه‌های خط‌لوله باشند (`<exam_id>_q<NNN>`، `…_o<برچسب>`، `…_i<n>`).
فیلدی که در gold نیامده سنجیده نمی‌شود. gold با truth برابر null یعنی «پاسخنامه نمی‌گوید» و خط‌لوله هم باید null بدهد.

معیارها (همه به‌صورت عدد و فهرست خطا):
  answer_link   : دقت اتصال پاسخ (مجموعه‌ی کلید برابر؛ اگر answer_status در gold هست، آن هم)
  fields        : دقت هر فیلد دیگر (structure، polarity، route، question_type، answer_status، context_dependency، main_topic)
  truth         : correct/wrong/abstained روی gold معلوم؛ precision (دقت وقتی خط‌لوله جواب می‌دهد)، coverage، accuracy کل؛
                  false_positive روی gold=null؛ پیش‌بینی‌های basis=manual (بازبین انسانی) از دقت حذف و جدا شمرده می‌شوند
  review        : درصد needs_review؛ error_catch_rate (چند درصد از truth‌های اشتباه پرچم بازبینی داشتند)؛ false_alarm_rate"""
from collections import Counter, defaultdict
from pathlib import Path

from .utils import read_jsonl, write_json

QUESTION_FIELDS = ("structure", "polarity", "route", "question_type", "answer_status", "context_dependency")


def load_gold(gold_dir):
    qs, ss = {}, {}
    files = sorted(Path(gold_dir).glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"هیچ فایل *.jsonl در {gold_dir} نیست (به gold/README.md نگاه کنید).")
    for f in files:
        for r in read_jsonl(f):
            if "statement_id" in r:
                ss[r["statement_id"]] = r
            elif "question_id" in r:
                qs[r["question_id"]] = r
    return qs, ss


def _ratio(a, b):
    return round(a / b, 4) if b else None


def evaluate(pred_questions, pred_statements, gold_questions, gold_statements):
    pq = {q["question_id"]: q for q in pred_questions}
    ps = {s["statement_id"]: s for s in pred_statements}
    rep = {}

    # --- سوال‌ها
    matched = [(g, pq[i]) for i, g in gold_questions.items() if i in pq]
    link_ok, link_err, n_link = 0, [], 0
    fields = {f: [0, 0, []] for f in QUESTION_FIELDS + ("main_topic",)}
    for g, p in matched:
        if "correct" in g:
            n_link += 1
            same = set(g["correct"]) == set(p["correct"]) and ("answer_status" not in g or g["answer_status"] == p["answer_status"])
            if same:
                link_ok += 1
            else:
                link_err.append({"id": g["question_id"], "gold": g["correct"], "pred": p["correct"]})
        for f in QUESTION_FIELDS:
            if f in g:
                fields[f][1] += 1
                if g[f] == p.get(f):
                    fields[f][0] += 1
                else:
                    fields[f][2].append({"id": g["question_id"], "gold": g[f], "pred": p.get(f)})
        if g.get("topic_ids"):
            fields["main_topic"][1] += 1
            if g["topic_ids"][0] == (p.get("topic_ids") or [None])[0]:
                fields["main_topic"][0] += 1
            else:
                fields["main_topic"][2].append({"id": g["question_id"], "gold": g["topic_ids"][0], "pred": (p.get("topic_ids") or [None])[0]})
    rep["questions"] = {
        "gold": len(gold_questions), "matched": len(matched), "coverage": _ratio(len(matched), len(gold_questions)),
        "missing_ids": sorted(set(gold_questions) - set(pq))}
    rep["answer_link"] = {"evaluated": n_link, "correct": link_ok, "accuracy": _ratio(link_ok, n_link), "errors": link_err}
    rep["fields"] = {f: {"evaluated": n, "correct": c, "accuracy": _ratio(c, n), "errors": e}
                     for f, (c, n, e) in fields.items() if n}

    # --- گزاره‌ها
    st_matched = [(g, ps[i]) for i, g in gold_statements.items() if i in ps]
    correct = wrong = abstained = gold_known = gold_null = fp = null_ok = manual = 0
    wrong_list, by_basis, by_struct = [], defaultdict(Counter), defaultdict(Counter)
    flagged_wrong = flagged_correct = n_correct_all = 0
    for g, p in st_matched:
        if "truth" not in g:
            continue
        if p["truth_basis"] == "manual":
            manual += 1
            continue
        struct = g.get("structure") or p.get("structure")
        if g["truth"] is None:
            gold_null += 1
            if p["truth"] is None:
                null_ok += 1
            else:
                fp += 1
                wrong_list.append({"id": g["statement_id"], "gold": None, "pred": p["truth"]})
            continue
        gold_known += 1
        if p["truth"] is None:
            abstained += 1
            by_basis[p["truth_basis"]]["abstained"] += 1
            by_struct[struct]["abstained"] += 1
        elif p["truth"] == g["truth"]:
            correct += 1
            n_correct_all += 1
            flagged_correct += bool(p["needs_review"])
            by_basis[p["truth_basis"]]["correct"] += 1
            by_struct[struct]["correct"] += 1
        else:
            wrong += 1
            flagged_wrong += bool(p["needs_review"])
            wrong_list.append({"id": g["statement_id"], "gold": g["truth"], "pred": p["truth"], "basis": p["truth_basis"],
                               "flagged": bool(p["needs_review"]), "reasons": p["review_reasons"]})
            by_basis[p["truth_basis"]]["wrong"] += 1
            by_struct[struct]["wrong"] += 1
    rep["statements"] = {"gold": len(gold_statements), "matched": len(st_matched),
                         "coverage": _ratio(len(st_matched), len(gold_statements)),
                         "missing_ids": sorted(set(gold_statements) - set(ps))}
    rep["truth"] = {
        "gold_known": gold_known, "correct": correct, "wrong": wrong, "abstained": abstained,
        "precision": _ratio(correct, correct + wrong), "answer_coverage": _ratio(correct + wrong, gold_known),
        "accuracy": _ratio(correct, gold_known),
        "gold_null": gold_null, "null_agreed": null_ok, "false_positive_on_gold_null": fp,
        "excluded_manual": manual, "errors": wrong_list,
        "by_basis": {k: dict(v) for k, v in by_basis.items()},
        "by_structure": {str(k): dict(v) for k, v in by_struct.items()}}
    all_s = list(ps.values())
    nr = sum(1 for s in all_s if s["needs_review"])
    rep["review"] = {
        "statements_total": len(all_s), "needs_review": nr, "needs_review_rate": _ratio(nr, len(all_s)),
        "error_catch_rate": _ratio(flagged_wrong, wrong) if wrong else None,
        "false_alarm_rate": _ratio(flagged_correct, n_correct_all) if n_correct_all else None}
    return rep


def format_report(rep):
    pct = lambda x: "—" if x is None else f"{x * 100:.1f}٪"
    t, r, a = rep["truth"], rep["review"], rep["answer_link"]
    L = ["== گزارش ارزیابی ==",
         f"سوال‌های gold: {rep['questions']['gold']} (یافت‌شده در خروجی: {rep['questions']['matched']}، پوشش {pct(rep['questions']['coverage'])})",
         f"دقت اتصال پاسخ: {pct(a['accuracy'])} ({a['correct']}/{a['evaluated']})"]
    for f, v in rep["fields"].items():
        L.append(f"دقت {f}: {pct(v['accuracy'])} ({v['correct']}/{v['evaluated']})")
    L += [f"گزاره‌های gold: {rep['statements']['gold']} (یافت‌شده: {rep['statements']['matched']}، پوشش {pct(rep['statements']['coverage'])})",
          f"truth: روی {t['gold_known']} گزاره‌ی معلوم — درست {t['correct']}، غلط {t['wrong']}، null (خودداری) {t['abstained']}",
          f"   دقتِ وقتی جواب می‌دهد (precision): {pct(t['precision'])}؛ پوشش پاسخ: {pct(t['answer_coverage'])}؛ دقت کل: {pct(t['accuracy'])}",
          f"   gold=null: {t['gold_null']} (هم‌خوان {t['null_agreed']}، truth ادعا‌شده‌ی بی‌مبنا {t['false_positive_on_gold_null']})",
          f"   پیش‌بینی‌های بازبین انسانی (manual) از دقت حذف شدند: {t['excluded_manual']}",
          f"needs_review: {pct(r['needs_review_rate'])} ({r['needs_review']}/{r['statements_total']})؛ "
          f"سهم خطاهای truth که پرچم داشتند: {pct(r['error_catch_rate'])}؛ هشدار اضافی روی درست‌ها: {pct(r['false_alarm_rate'])}"]
    return "\n".join(L)


def run_evaluate(project_dir, gold_dir, log=print):
    pd = Path(project_dir)
    gq, gs = load_gold(gold_dir)
    for f in ("questions.jsonl", "statements.jsonl"):
        if not (pd / f).exists():
            raise RuntimeError(f"{f} نیست؛ ابتدا export را اجرا کنید.")
    rep = evaluate(read_jsonl(pd / "questions.jsonl"), read_jsonl(pd / "statements.jsonl"), gq, gs)
    write_json(pd / "evaluation_report.json", rep)
    log(format_report(rep))
    return rep
