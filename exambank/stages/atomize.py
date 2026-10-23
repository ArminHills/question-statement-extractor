# -*- coding: utf-8 -*-
"""مرحله ۴ و ۴ب: atomize (گزاره‌ها) و problems (مسئله‌ها). classified.jsonl ← statements_raw.jsonl، problems_raw.jsonl.

اصل‌ها:
  • صحیح/غلط را فقط کد تعیین می‌کند (truth_rules). LLM متن می‌سازد و حکم صریح پاسخ تشریحی را با نقل عین گزارش می‌کند.
  • هر ادعای LLM راستی‌آزمایی می‌شود: evidence/why_false باید عیناً در پاسخ تشریحی باشد؛ متن آیتم‌ها باید در صورت سوال باشد؛
    معنای گزینه‌های combo/count با تحلیل‌گر قاعده‌محور cross-check می‌شود؛ text_standalone نباید واژه‌ی تازه‌ی زیادی بیاورد.
  • خروجی خام LLM در work/ ذخیره می‌شود و ساخت رکوردها (build_outputs) بدون LLM قابل اجرای دوباره است.
"""
import json
import re
from pathlib import Path

from ..constants import SCHEMA_VERSION
from ..normalize import normalize_label, normalize_text
from ..parsing import contains_quote, novel_token_ratio, parse_combo_option, parse_count_option
from ..prompts import load_prompt
from ..runner import run_items
from ..truth_rules import COMBO_LIKE, COUNT_LIKE, SINGLE_LIKE, ComboOption, derive_truth
from ..utils import read_json, read_jsonl, write_json, write_jsonl
from .classify import work_path

NOVEL_MAX = 0.5
KNOWN_PROBLEM_CLASSES = ("genetics_cross", "pedigree", "probability_counting", "dna_rna_calc", "cell_division_count",
                         "physiology_calc", "graph_data_interpretation", "experiment_inference", "logic_inference",
                         "other")
SEM_KINDS = ("set", "all", "none", "count", "unknown")
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


# ------------------------------------------------------------ اعتبارسنجی LLM
def _verdict(v, where, errors):
    if v is None:
        return None
    if not isinstance(v, dict) or not isinstance(v.get("truth"), bool) or \
            not isinstance(v.get("evidence"), str) or not v["evidence"].strip():
        errors.append(f"{where}.explicit_verdict نامعتبر (truth بولی و evidence غیرخالی لازم است)")
        return None
    return {"truth": v["truth"], "evidence": v["evidence"].strip()}


def validate_atomize_payload(data):
    errors = []
    if not isinstance(data, dict) or not isinstance(data.get("statements"), list) or not data["statements"]:
        raise ValueError("statements باید لیست غیرخالی باشد")
    stmts, seen = [], set()
    for i, s in enumerate(data["statements"]):
        w = f"statements[{i}]"
        if not isinstance(s, dict) or not isinstance(s.get("text_original"), str) or \
                not isinstance(s.get("text_standalone"), str) or not isinstance(s.get("target"), (str, int)):
            errors.append(f"{w} نامعتبر")
            continue
        t = normalize_label(s["target"])
        if not t or t in seen:
            errors.append(f"{w}: target خالی یا تکراری ({t!r})")
            continue
        seen.add(t)
        parts = s.get("parts")
        norm_parts = None
        if parts:
            norm_parts = []
            for k, p in enumerate(parts):
                v = _verdict(p.get("explicit_verdict") if isinstance(p, dict) else None, f"{w}.parts[{k}]", errors)
                if not isinstance(p, dict) or not isinstance(p.get("text_original"), str) or \
                        not isinstance(p.get("text_standalone"), str) or v is None:
                    errors.append(f"{w}.parts[{k}] نامعتبر (هر جزء حکم صریح لازم دارد)")
                    continue
                norm_parts.append({"text_original": p["text_original"], "text_standalone": p["text_standalone"],
                                   "explicit_verdict": v})
        stmts.append({
            "target": t, "text_original": s["text_original"], "text_standalone": s["text_standalone"],
            "is_atomic": bool(s.get("is_atomic", True)), "standalone": bool(s.get("standalone", True)),
            "context_ref": s.get("context_ref") if isinstance(s.get("context_ref"), str) else None,
            "explicit_verdict": _verdict(s.get("explicit_verdict"), w, errors),
            "why_false": s["why_false"].strip() if isinstance(s.get("why_false"), str) and s["why_false"].strip() else None,
            "distractor_type": s.get("distractor_type") if isinstance(s.get("distractor_type"), str) else None,
            "parts": norm_parts})
    sems = []
    for i, m in enumerate(data.get("option_semantics") or []):
        if not isinstance(m, dict) or m.get("kind") not in SEM_KINDS or not isinstance(m.get("label"), (str, int)):
            errors.append(f"option_semantics[{i}] نامعتبر")
            continue
        cnt = m.get("count")
        if cnt is not None and (isinstance(cnt, bool) or not isinstance(cnt, int)):
            errors.append(f"option_semantics[{i}].count عدد صحیح نیست")
            continue
        sems.append({"label": normalize_label(m["label"]), "kind": m["kind"],
                     "items": [normalize_label(x) for x in (m.get("items") or [])], "count": cnt})
    if errors:
        raise ValueError("; ".join(errors))
    return {"statements": stmts, "option_semantics": sems}


def validate_problem_payload(data):
    errors = []
    if not isinstance(data, dict):
        raise ValueError("خروجی باید شیء JSON باشد")
    pc = data.get("problem_class")
    if not isinstance(pc, str) or not _SNAKE.match(pc):
        errors.append(f"problem_class باید snake_case باشد: {pc!r}")
    givens = data.get("givens", [])
    if not isinstance(givens, list) or not all(isinstance(g, str) and g.strip() for g in givens):
        errors.append("givens باید لیست رشته‌ی غیرخالی باشد")
    asked = data.get("asked")
    if asked is not None and not isinstance(asked, str):
        errors.append("asked رشته یا null باشد")
    par = data.get("parametrization")
    norm_par = None
    if par is not None:
        if not isinstance(par, dict) or not isinstance(par.get("is_candidate"), bool) or not isinstance(par.get("slots", []), list):
            errors.append("parametrization نامعتبر")
        else:
            slots = []
            for i, s in enumerate(par.get("slots", [])):
                if not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s[k].strip() for k in ("name", "value")) \
                        or s.get("role") not in ("given", "asked", "constant"):
                    errors.append(f"parametrization.slots[{i}] نامعتبر")
                    continue
                slots.append({"name": s["name"].strip(), "value": s["value"].strip(), "role": s["role"]})
            norm_par = {"is_candidate": par["is_candidate"], "slots": slots}
    if errors:
        raise ValueError("; ".join(errors))
    return {"problem_class": pc, "givens": [g.strip() for g in givens],
            "asked": asked.strip() if isinstance(asked, str) and asked.strip() else None, "parametrization": norm_par}


# ---------------------------------------------------------- معنای گزینه‌ها
def _combo_from_sem(sem):
    if sem is None:
        return None
    if sem["kind"] == "all":
        return ComboOption("all")
    if sem["kind"] == "none":
        return ComboOption("none")
    if sem["kind"] == "set" and sem["items"]:
        return ComboOption("set", tuple(sem["items"]))
    return None


def _as_set(opt, labels):
    if opt is None:
        return None
    return set(labels) if opt.kind == "all" else (set() if opt.kind == "none" else set(opt.items))


def resolve_semantics(structure, options, item_labels, sems, correct):
    """-> (combo_dict, counts_dict, reasons). تحلیل‌گر قاعده‌محور اولویت دارد؛ LLM فقط جایگزین یا cross-check است.
    پرچم فقط برای گزینه‌ی کلید زده می‌شود (گزینه‌های دیگر در truth نقشی ندارند)."""
    by_label = {s["label"]: s for s in sems}
    combo, counts, reasons = {}, {}, []
    for o in options:
        lab, text = o["label"], o["text_original"]
        is_key = lab in correct
        sem = by_label.get(lab)
        if structure in COUNT_LIKE:
            parsed = parse_count_option(text)
            llm = sem["count"] if sem and sem["kind"] == "count" else None
            if parsed is not None:
                counts[lab] = parsed
                if is_key and llm is not None and llm != parsed:
                    reasons.append("option_parse_disagreement")
            elif llm is not None:
                counts[lab] = llm
                if is_key:
                    reasons.append("option_semantics_llm_only")
        elif structure in COMBO_LIKE:
            parsed = parse_combo_option(text, item_labels)
            llm = _combo_from_sem(sem)
            if parsed is not None:
                combo[lab] = parsed
                if is_key and llm is not None and _as_set(parsed, item_labels) != _as_set(llm, item_labels):
                    reasons.append("option_parse_disagreement")
            elif llm is not None:
                combo[lab] = llm
                if is_key:
                    reasons.append("option_semantics_llm_only")
    return combo, counts, sorted(set(reasons))


# ------------------------------------------------------------ ساخت گزاره‌ها
def _source(q, label):
    e = q["exam"]
    return {"exam_id": e["exam_id"], "year_jalali": e.get("year_jalali"), "group": e.get("group"),
            "question_no": q["number"], "option_label": label, "pages": q["pages"]}


def build_statements(q, llm):
    """-> (statements, question_level_reasons). q: رکورد classified؛ llm: خروجی validate_atomize_payload."""
    structure, polarity = q["structure"], q["polarity"]
    expl = q.get("explanation_original") or ""
    qid = q["question_id"]
    base_reasons = list(q.get("review_reasons", []))
    llm_by = {s["target"]: s for s in llm["statements"]}
    opts = {o["label"]: o for o in q["options"]}

    if structure in SINGLE_LIKE:
        targets = [o["label"] for o in q["options"]]
        extra = [t for t in llm_by if t not in opts]
        if extra:
            base_reasons.append("llm_extra_statement")
        prefix = "o"
    else:
        targets = [s["target"] for s in llm["statements"]]
        if len(targets) < 2:
            return [], base_reasons + ["items_missing"]
        prefix = "i"

    combo = counts = None
    sem_reasons = []
    if structure in COMBO_LIKE or structure in COUNT_LIKE:
        combo, counts, sem_reasons = resolve_semantics(structure, q["options"], targets, llm["option_semantics"],
                                                       q.get("correct", []))

    explicit, ev_bad = {}, {}
    for t in targets:
        s = llm_by.get(t)
        v = s["explicit_verdict"] if s else None
        if v:
            if contains_quote(expl, v["evidence"]):
                explicit[t] = (v["truth"], v["evidence"])
            else:
                ev_bad[t] = "evidence_not_found"

    results = {r.target: r for r in derive_truth(
        structure=structure, polarity=polarity, answer_status=q["answer_status"], correct=q.get("correct", []),
        targets=targets, combo=combo, counts=counts, explicit=explicit)}

    valid_refs = set(q.get("figure_ids", [])) | {"shared_scenario"}
    out = []
    for idx, t in enumerate(targets, start=1):
        s = llm_by.get(t)
        reasons = list(base_reasons) + list(sem_reasons) + list(results[t].review_reasons)
        if t in ev_bad:
            reasons.append(ev_bad[t])
        if s is None:
            reasons.append("llm_statement_missing")
        if structure in SINGLE_LIKE:
            text_original = opts[t]["text_original"]
            sources = (q["stem_original"], text_original)
        else:
            text_original = s["text_original"]
            sources = (q["stem_original"],)
            if not contains_quote(q["stem_original"], text_original):
                reasons.append("item_text_not_in_stem")
        standalone_text = (s["text_standalone"].strip() if s else "") or ""
        if not standalone_text:
            standalone_text = text_original
            reasons.append("standalone_missing")
        elif novel_token_ratio(standalone_text, *sources) > NOVEL_MAX:
            reasons.append("standalone_adds_words")
        standalone = s["standalone"] if s else True
        ref = s["context_ref"] if s else None
        if not standalone:
            if ref not in valid_refs:
                reasons.append("context_ref_invalid")
                ref = None
            if q.get("context_dependency") == "none":
                reasons.append("context_inconsistent")
        else:
            ref = None
        res = results[t]
        why = None
        distractor = None
        if s and s["why_false"]:
            if res.truth is False and contains_quote(expl, s["why_false"]):
                why, distractor = s["why_false"], s["distractor_type"]
            elif res.truth is False:
                reasons.append("why_false_not_found")
        sid = f"{qid}_{prefix}{t if prefix == 'o' else idx}"
        parts_ok = False
        if s and s["parts"]:
            parts_ok = all(contains_quote(expl, p["explicit_verdict"]["evidence"]) and
                           contains_quote(text_original, p["text_original"]) for p in s["parts"])
            if not parts_ok:
                reasons.append("parts_rejected")
        is_atomic = bool(s["is_atomic"]) if s else True
        if parts_ok:
            is_atomic = False
        rec = {
            "schema_version": SCHEMA_VERSION, "statement_id": sid, "question_id": qid,
            "text_original": text_original, "text_standalone": standalone_text,
            "truth": res.truth, "truth_basis": res.basis, "truth_evidence": res.evidence,
            "why_false": why, "distractor_type": distractor,
            "is_atomic": is_atomic, "standalone": standalone,
            "context_dependency": q.get("context_dependency", "none") if not standalone else "none",
            "context_ref": ref, "topic_ids": list(q.get("topic_ids", [])),
            "question_type": q["question_type"], "structure": structure, "polarity": polarity,
            "source": _source(q, t), "dedupe": {"canonical_id": sid, "occurrences": 1},
            "needs_review": False, "review_reasons": [], "review_status": "pending",
            "llm_confidence": q.get("llm_confidence", 0.0), "parent_statement_id": None,
        }
        rec["review_reasons"] = sorted(set(reasons))
        rec["needs_review"] = bool(rec["review_reasons"])
        out.append(rec)
        if parts_ok:
            for k, p in enumerate(s["parts"], start=1):
                part = dict(rec)
                part.update({
                    "statement_id": f"{sid}_p{k}", "text_original": p["text_original"],
                    "text_standalone": p["text_standalone"], "truth": p["explicit_verdict"]["truth"],
                    "truth_basis": "explanation", "truth_evidence": p["explicit_verdict"]["evidence"],
                    "why_false": None, "distractor_type": None, "is_atomic": True, "parent_statement_id": sid,
                    "dedupe": {"canonical_id": f"{sid}_p{k}", "occurrences": 1}})
                part["review_reasons"] = sorted(set(base_reasons))
                part["needs_review"] = bool(part["review_reasons"])
                out.append(part)
    return out, base_reasons


# --------------------------------------------------------------- مسئله‌ها
def build_problem(q, llm):
    reasons = list(q.get("review_reasons", []))
    stem = q["stem_original"]
    for g in llm["givens"]:
        if not contains_quote(stem, g):
            reasons.append("given_not_in_stem")
    if llm["asked"] and not contains_quote(stem, llm["asked"]):
        reasons.append("asked_not_in_stem")
    par = llm["parametrization"]
    if par is not None:
        kept = []
        for s in par["slots"]:
            if contains_quote(stem, s["value"]):
                kept.append(s)
            else:
                reasons.append("slot_value_not_in_stem")
        par = {"is_candidate": par["is_candidate"] and bool(kept), "slots": kept}
        if llm["parametrization"]["is_candidate"] and not kept:
            reasons.append("parametrization_dropped")
    if llm["problem_class"] not in KNOWN_PROBLEM_CLASSES:
        reasons.append("new_problem_class")
    pid = f"{q['question_id']}_prob"
    reasons = sorted(set(reasons))
    return {
        "schema_version": SCHEMA_VERSION, "problem_id": pid, "question_id": q["question_id"],
        "stem_original": stem, "stem": q["stem"],
        "options": [{"label": o["label"], "text_original": o["text_original"], "text": o["text"]} for o in q["options"]],
        "correct": list(q.get("correct", [])), "answer_status": q["answer_status"],
        "solution_text": q.get("explanation_original"), "problem_class": llm["problem_class"],
        "givens": llm["givens"], "asked": llm["asked"], "figure_refs": list(q.get("figure_ids", [])),
        "topic_ids": list(q.get("topic_ids", [])), "question_type": q["question_type"],
        "parametrization": par, "source": _source(q, None), "needs_review": bool(reasons),
        "review_reasons": reasons, "review_status": "pending", "llm_confidence": q.get("llm_confidence", 0.0)}


# ------------------------------------------------------------------ اجرا
def _input_json(q):
    return json.dumps({
        "structure": q["structure"], "polarity": q["polarity"], "stem": q["stem_original"],
        "options": [{"label": o["label"], "text": o["text_original"]} for o in q["options"]],
        "explanation": q.get("explanation_original"),
        "figures": [{"figure_id": f["figure_id"], "kind": f["kind"], "description": f["description_fa"]}
                    for f in q.get("figures", [])]}, ensure_ascii=False)


def _problem_input_json(q):
    return json.dumps({
        "stem": q["stem_original"],
        "options": [{"label": o["label"], "text": o["text_original"]} for o in q["options"]],
        "explanation": q.get("explanation_original"),
        "figures": [{"figure_id": f["figure_id"], "kind": f["kind"], "description": f["description_fa"]}
                    for f in q.get("figures", [])]}, ensure_ascii=False)


def run_atomize(*, project_dir, client, model, state, log=print, delay=3, stop_event=None, sleep=None,
                max_consecutive_rate_limits=2):
    import time
    sleep = sleep or time.sleep
    recs = read_jsonl(Path(project_dir) / "classified.jsonl")
    by_id = {r["question_id"]: r for r in recs}
    atom_prompt, prob_prompt = load_prompt("atomize"), load_prompt("problem")
    keys = []
    for r in recs:
        if r["route"] in ("statement", "mixed"):
            keys.append(f"{r['question_id']}:atomize")
        if r["route"] in ("problem", "mixed"):
            keys.append(f"{r['question_id']}:problem")

    def work(key):
        qid, kind = key.rsplit(":", 1)
        q = by_id[qid]
        if kind == "atomize":
            data = client.generate_json(task="atomize", system_prompt=atom_prompt, parts=[("text", _input_json(q))],
                                        schema=None, validator=validate_atomize_payload, model=model)
            write_json(work_path(project_dir, "atomize", qid), validate_atomize_payload(data))
        else:
            data = client.generate_json(task="problem", system_prompt=prob_prompt,
                                        parts=[("text", _problem_input_json(q))], schema=None,
                                        validator=validate_problem_payload, model=model)
            write_json(work_path(project_dir, "problem", qid), validate_problem_payload(data))
        log(f"[SUCCESS] {key}")

    summary = run_items(keys, work, state, "atomize", log=log, delay=delay, stop_event=stop_event, sleep=sleep,
                        max_consecutive_rate_limits=max_consecutive_rate_limits)
    build_outputs(project_dir)
    return summary


def build_outputs(project_dir):
    """work/ + classified.jsonl ← statements_raw.jsonl، problems_raw.jsonl، atomize_report.json (بدون LLM)."""
    recs = read_jsonl(Path(project_dir) / "classified.jsonl")
    statements, problems = [], []
    report = {"missing_atomize": [], "missing_problem": [], "items_missing": []}
    for q in recs:
        qid = q["question_id"]
        if q["route"] in ("statement", "mixed"):
            llm = read_json(work_path(project_dir, "atomize", qid))
            if llm is None:
                report["missing_atomize"].append(qid)
            else:
                st, _ = build_statements(q, llm)
                if not st:
                    report["items_missing"].append(qid)
                statements.extend(st)
        if q["route"] in ("problem", "mixed"):
            llm = read_json(work_path(project_dir, "problem", qid))
            if llm is None:
                report["missing_problem"].append(qid)
            else:
                problems.append(build_problem(q, llm))
    write_jsonl(Path(project_dir) / "statements_raw.jsonl", statements)
    write_jsonl(Path(project_dir) / "problems_raw.jsonl", problems)
    report.update(statements=len(statements), problems=len(problems),
                  statements_null_truth=sum(1 for s in statements if s["truth"] is None),
                  statements_needs_review=sum(1 for s in statements if s["needs_review"]))
    write_json(Path(project_dir) / "atomize_report.json", report)
    return statements, problems, report
