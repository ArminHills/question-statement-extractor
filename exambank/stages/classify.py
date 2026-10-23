# -*- coding: utf-8 -*-
"""مرحله ۳: classify. assembled.jsonl ← classified.jsonl.
LLM فقط صورت سوال/گزینه/شکل را می‌بیند (نه کلید، نه پاسخ تشریحی). خروجی خام LLM در work/classify/<qid>.json
نگه‌داری می‌شود و بررسی‌های قاعده‌محور هنگام جمع‌کردن اعمال می‌شود (تغییر قواعد = بدون مصرف سهمیه)."""
import json
from pathlib import Path

from ..normalize import normalize_text
from ..parsing import contains_quote, options_look_like, polarity_hint
from ..prompts import load_prompt
from ..runner import run_items
from ..taxonomy import describe_for_prompt, topic_ids
from ..utils import read_json, read_jsonl, write_json, write_jsonl

QUESTION_TYPES = ("textbook_recall", "counting", "analytical", "inferential", "figure_based",
                  "graph_table_based", "experiment_based", "calculation", "other")
STRUCTURES = ("single_best_statement", "completion", "count_items", "combo_items", "matching", "ordering",
              "calculation", "other")
ROUTES = ("statement", "problem", "mixed")
CONTEXTS = ("none", "figure", "table", "shared_scenario")
EVAL_STRUCTURES = {"single_best_statement", "completion", "count_items", "combo_items", "matching", "ordering"}
TASK = "classify"


def validate_classify_payload(data, valid_topics):
    errors = []
    if not isinstance(data, dict):
        raise ValueError("خروجی باید شیء JSON باشد")

    def enum(key, allowed):
        v = data.get(key)
        if v not in allowed:
            errors.append(f"{key} نامعتبر: {v!r}")
        return v
    qt, st, route, ctx = (enum("question_type", QUESTION_TYPES), enum("structure", STRUCTURES),
                          enum("route", ROUTES), enum("context_dependency", CONTEXTS))
    pol = data.get("polarity")
    if pol not in (None, "positive", "negative"):
        errors.append(f"polarity نامعتبر: {pol!r}")
    ev = data.get("polarity_evidence")
    if pol is not None and not (isinstance(ev, str) and ev.strip()):
        errors.append("polarity بدون polarity_evidence")
    topics = data.get("topics")
    ids = []
    if not isinstance(topics, dict) or topics.get("main") not in valid_topics:
        errors.append(f"topics.main باید از taxonomy باشد: {(topics or {}).get('main')!r}" if isinstance(topics, dict)
                      else "topics باید شیء باشد")
    else:
        sec = topics.get("secondary", [])
        if not isinstance(sec, list) or len(sec) > 2 or any(t not in valid_topics for t in sec) \
                or len(set(sec)) != len(sec) or topics["main"] in sec:
            errors.append(f"topics.secondary نامعتبر: {sec!r}")
        else:
            ids = [topics["main"]] + sec
    conf = data.get("llm_confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        errors.append(f"llm_confidence باید عددی بین ۰ و ۱ باشد: {conf!r}")
    if errors:
        raise ValueError("; ".join(errors))
    return {"question_type": qt, "structure": st, "polarity": pol,
            "polarity_evidence": ev.strip() if pol else None, "topic_ids": ids, "route": route,
            "context_dependency": ctx, "llm_confidence": float(conf)}


def classification_reasons(rec, c):
    """پرچم‌های قاعده‌محور. LLM هرگز خودش رکوردی را «تأییدشده» نمی‌کند."""
    r = []
    pol, st, route = c["polarity"], c["structure"], c["route"]
    if pol and not contains_quote(rec["stem_original"], c["polarity_evidence"]):
        r.append("polarity_evidence_not_found")
    hint = polarity_hint(rec["stem_original"])
    if pol and hint and hint != pol:
        r.append("polarity_disagreement")
    if pol is None and route in ("statement", "mixed") and st in EVAL_STRUCTURES:
        r.append("polarity_missing")
    if st == "count_items" and options_look_like("count_items", rec["options"]) is False:
        r.append("structure_option_mismatch")
    if (route == "problem" and st in ("single_best_statement",)) or (st == "calculation" and route == "statement"):
        r.append("route_structure_mismatch")
    has_fig = bool(rec.get("figure_ids"))
    ctx = c["context_dependency"]
    if (has_fig and ctx == "none") or (not has_fig and ctx in ("figure", "table")):
        r.append("figure_context_mismatch")
    return r


def apply_classification(rec, cls):
    out = dict(rec)
    out.update(cls)
    out["review_reasons"] = classification_reasons(rec, cls)
    return out


def _input_json(rec):
    return json.dumps({
        "stem": rec["stem_original"],
        "options": [{"label": o["label"], "text": o["text_original"]} for o in rec["options"]],
        "figures": [{"figure_id": f["figure_id"], "kind": f["kind"], "description": f["description_fa"]}
                    for f in rec.get("figures", [])]}, ensure_ascii=False)


def work_path(project_dir, stage, qid):
    return Path(project_dir) / "work" / stage / f"{qid}.json"


def run_classify(*, project_dir, client, model, state, taxonomy, log=print, delay=3, stop_event=None,
                 sleep=None, max_consecutive_rate_limits=2):
    import time
    sleep = sleep or time.sleep
    recs = read_jsonl(Path(project_dir) / "assembled.jsonl")
    by_id = {r["question_id"]: r for r in recs}
    valid = set(topic_ids(taxonomy))
    system_prompt = load_prompt("classify") + "\n" + describe_for_prompt(taxonomy)

    def work(qid):
        rec = by_id[qid]
        data = client.generate_json(task=TASK, system_prompt=system_prompt, parts=[("text", _input_json(rec))],
                                    schema=None, validator=lambda d: validate_classify_payload(d, valid),
                                    model=model)
        cls = validate_classify_payload(data, valid)
        write_json(work_path(project_dir, "classify", qid), cls)
        log(f"[SUCCESS] {qid}: {cls['structure']} / {cls['route']} / {cls['polarity']}")

    summary = run_items(list(by_id), work, state, "classify", log=log, delay=delay, stop_event=stop_event,
                        sleep=sleep, max_consecutive_rate_limits=max_consecutive_rate_limits)
    gather_classified(project_dir)
    return summary


def gather_classified(project_dir):
    """work/classify/*.json + assembled.jsonl ← classified.jsonl (بدون LLM؛ قواعد دوباره اعمال می‌شوند)."""
    recs = read_jsonl(Path(project_dir) / "assembled.jsonl")
    out = []
    for r in recs:
        cls = read_json(work_path(project_dir, "classify", r["question_id"]))
        if cls:
            out.append(apply_classification(r, cls))
    write_jsonl(Path(project_dir) / "classified.jsonl", out)
    return out
