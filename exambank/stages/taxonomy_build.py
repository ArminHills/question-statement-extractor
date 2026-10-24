# -*- coding: utf-8 -*-
"""build-taxonomy: فهرست فصل/گفتار از صفحه‌های فهرست مطالبی که کاربر می‌دهد (نه از حافظه).
هر صفحه یک فراخوانی؛ فقط وقتی همه‌ی صفحه‌ها موفق بودند taxonomy نوشته می‌شود (ناقص ننویس)."""
from pathlib import Path

from ..normalize import normalize_text
from ..prompts import load_prompt
from ..client import request_counter_for
from ..runner import run_items
from ..taxonomy import DEFAULT_PATH, merge_toc_pages, save_book
from ..utils import read_json, write_json
from .extract import _single_page_bytes


def validate_toc_payload(data):
    if not isinstance(data, list):
        raise ValueError("خروجی باید لیست فصل‌ها باشد (برای نبودن فهرست، لیست خالی)")
    out, errors = [], []
    for i, ch in enumerate(data):
        w = f"[{i}]"
        try:
            n = int(normalize_text(str(ch["chapter_number"])))
        except (TypeError, ValueError, KeyError):
            errors.append(f"{w}: chapter_number نامعتبر")
            continue
        title = ch.get("chapter_title")
        if not isinstance(title, str) or not isinstance(ch.get("sections", []), list):
            errors.append(f"{w}: ساختار نامعتبر")
            continue
        secs = []
        for j, s in enumerate(ch.get("sections", [])):
            try:
                m = int(normalize_text(str(s["number"])))
            except (TypeError, ValueError, KeyError):
                errors.append(f"{w}.sections[{j}]: number نامعتبر")
                continue
            if not isinstance(s.get("title"), str) or not s["title"].strip():
                errors.append(f"{w}.sections[{j}]: عنوان خالی")
                continue
            secs.append({"number": m, "title": s["title"].strip()})
        out.append({"chapter_number": n, "chapter_title": title.strip(), "sections": secs})
    if errors:
        raise ValueError("; ".join(errors))
    return out


def run_build_taxonomy(*, pdf_path, pages, project_dir, client, model, state, book_id, title, grade,
                       taxonomy_path=None, force=False, log=print, delay=3, stop_event=None, sleep=None,
                       max_consecutive_rate_limits=2):
    import time
    from pypdf import PdfReader
    sleep = sleep or time.sleep
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    valid = [p for p in pages if 1 <= p <= total]
    for p in pages:
        if p not in valid:
            log(f"[WARN] صفحه {p} در PDF نیست؛ رد شد.")
    prompt = load_prompt("toc")
    stage = f"taxonomy:{book_id}"
    wdir = Path(project_dir) / "work" / "taxonomy"

    def work(key):
        page = int(key.split(":")[-1])
        data = client.generate_json(task="toc_page", system_prompt=prompt,
                                    parts=[("pdf", _single_page_bytes(reader, page))], schema=None,
                                    validator=validate_toc_payload, model=model)
        write_json(wdir / f"{book_id}_p{page:04d}.json", validate_toc_payload(data))
        log(f"[SUCCESS] فهرست صفحه {page}")

    summary = run_items([f"{book_id}:{p}" for p in valid], work, state, stage, log=log, delay=delay,
                        stop_event=stop_event, sleep=sleep, max_consecutive_rate_limits=max_consecutive_rate_limits,
                        request_counter=request_counter_for(client))
    if not summary.ok:
        log("[WARN] همه‌ی صفحه‌های فهرست موفق نبودند؛ taxonomy نوشته نشد.")
        return summary, None
    results = [read_json(wdir / f"{book_id}_p{p:04d}.json") for p in valid]
    book = merge_toc_pages(book_id, title, grade, results, source=Path(pdf_path).name + f" p.{valid[0]}-{valid[-1]}")
    path = taxonomy_path or DEFAULT_PATH
    tax = save_book(path, book, force=force)
    n_ch = len(book["chapters"])
    n_sec = sum(len(c["sections"]) for c in book["chapters"])
    log(f"[INFO] taxonomy نسخه‌ی {tax['version']} نوشته شد: {path} — {n_ch} فصل، {n_sec} گفتار از «{title}». "
        "لطفاً فایل را با کتاب مقایسه و در صورت نیاز با دست ویرایش کنید.")
    return summary, tax
