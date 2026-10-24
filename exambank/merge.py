# -*- coding: utf-8 -*-
"""merge: ادغام چند پروژه در یک بانک اصلی با شناسه‌های پایدار و upsert بدون تکرار.

ادغام روی validated/ (پایه‌ی قبل از ویرایش و dedupe) انجام می‌شود و ویرایش‌های بازبین جدا (review_edits.jsonl، اتحاد با
برتری تازه‌تر) می‌مانند؛ بعد export روی بانک اصلی اجرا می‌شود. پس ادغام دوباره، هیچ ویرایش دستی را از بین نمی‌برد و
dedupe/تعارض truth بین پروژه‌ها (سال‌های مختلف) روی کل بانک محاسبه می‌شود."""
import json
from pathlib import Path

from .review import load_edits, merge_edits, save_edits
from .stages.export import export_project
from .utils import read_json, read_jsonl, write_json, write_jsonl

KINDS = (("questions", "question_id"), ("statements", "statement_id"), ("problems", "problem_id"))


def merge_projects(master_dir, project_dirs, *, similarity_threshold=90, sqlite=False, log=print):
    master = Path(master_dir)
    base = master / "validated"
    store = {}
    for kind, idf in KINDS:
        p = base / f"{kind}.jsonl"
        store[kind] = {r[idf]: r for r in read_jsonl(p)} if p.exists() else {}
    edits = load_edits(master)
    issues = {json.dumps(i, ensure_ascii=False, sort_keys=True): i
              for i in (read_json(master / "assemble_report.json") or {}).get("issues", [])}
    stats = {k: {"added": 0, "updated": 0} for k, _ in KINDS}
    for pd in map(Path, project_dirs):
        for kind, idf in KINDS:
            p = pd / "validated" / f"{kind}.jsonl"
            if not p.exists():
                raise RuntimeError(f"{pd}: validated/{kind}.jsonl نیست؛ ابتدا validate (و export) را روی آن پروژه اجرا کنید.")
            for r in read_jsonl(p):
                stats[kind]["updated" if r[idf] in store[kind] else "added"] += 1
                store[kind][r[idf]] = r
        edits = merge_edits(edits, load_edits(pd))
        for i in (read_json(pd / "assemble_report.json") or {}).get("issues", []):
            issues[json.dumps(i, ensure_ascii=False, sort_keys=True)] = i
    for kind, idf in KINDS:
        write_jsonl(base / f"{kind}.jsonl", [store[kind][k] for k in sorted(store[kind])])
    save_edits(master, edits)
    write_json(master / "assemble_report.json", {"questions": len(store["questions"]), "issues": list(issues.values())})
    log(f"[INFO] merge: " + "، ".join(f"{k}: {v['added']} جدید، {v['updated']} به‌روز" for k, v in stats.items()))
    report = export_project(master, similarity_threshold=similarity_threshold, sqlite=sqlite, log=log)
    return stats, report
