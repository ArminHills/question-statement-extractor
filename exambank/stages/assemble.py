# -*- coding: utf-8 -*-
"""مرحله ۲: assemble (بدون LLM). pages/*.json ← assembled.jsonl + assemble_report.json."""
import glob
import json
from pathlib import Path

from ..assemble import Manifest, link_and_check, stitch_pages
from ..constants import SCHEMA_VERSION
from ..normalize import normalize_text
from ..utils import read_json, write_json, write_jsonl


def load_pages(project_dir, pdf_key=None):
    out = []
    for f in sorted(glob.glob(str(Path(project_dir) / "pages" / "*.json"))):
        rec = read_json(f)
        if rec and (pdf_key is None or rec["pdf"] == pdf_key):
            out.append(rec)
    return out


def finalize(records, pages):
    """فیلدهای نرمال‌شده و figure_ids. فیلدهای *_original دست‌نخورده می‌مانند."""
    fig_by_q = {}
    fig_info = {}
    for p in pages:
        for fig in p.get("figures", []):
            fig_info[fig["figure_id"]] = {"figure_id": fig["figure_id"], "kind": fig["kind"],
                                          "description_fa": fig["description_fa"],
                                          "image_path": fig.get("image_path")}
            for n in fig.get("question_numbers", []):
                fig_by_q.setdefault((p["pdf"], p["page"], n), []).append(fig["figure_id"])
    unlinked = []
    for p in pages:
        for fig in p.get("figures", []):
            if not fig.get("question_numbers"):
                unlinked.append(fig["figure_id"])
    for r in records:
        r["schema_version"] = SCHEMA_VERSION
        r["stem"] = normalize_text(r["stem_original"])
        r["options"] = [{"label": o["label"], "text_original": o["text"], "text": normalize_text(o["text"])}
                        for o in r["options"]]
        r["explanation"] = normalize_text(r["explanation_original"]) if r["explanation_original"] else None
        ids = []
        for pg in r["pages"]["question"]:
            for fid in fig_by_q.get((r["pdf"], pg, r["number"]), []):
                if fid not in ids:
                    ids.append(fid)
        r["figure_ids"] = ids
        r["figures"] = [fig_info[i] for i in ids]
        r["question_id"] = f"{r['exam']['exam_id']}_q{r['number']:03d}"
    return records, unlinked


def run_assemble(project_dir, manifest: Manifest, layout="auto", expected_option_counts=(4,), pdf_key=None):
    pages = load_pages(project_dir, pdf_key)
    if not pages:
        raise RuntimeError("هیچ فایل صفحه‌ای در pages/ نیست؛ ابتدا extract را اجرا کنید.")
    questions, answers, stitch_issues = stitch_pages(pages)
    records, issues = link_and_check(questions, answers, manifest, layout, expected_option_counts, stitch_issues)
    records, unlinked = finalize(records, pages)
    for fid in unlinked:
        issues.append({"code": "figure_unlinked", "exam_id": None, "number": None, "pages": [], "detail": fid})
    write_jsonl(Path(project_dir) / "assembled.jsonl", records)
    counts = {}
    for i in issues:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    write_json(Path(project_dir) / "assemble_report.json",
               {"questions": len(records), "issue_counts": counts, "issues": issues})
    return records, issues
