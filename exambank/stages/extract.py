# -*- coding: utf-8 -*-
"""مرحله ۱: extract — هر صفحه یک فراخوانی (PDF تک‌صفحه‌ای، دید مستقیم)، خروجی JSON روی دیسک.
خروجی هر صفحه: <project>/pages/<pdf_key>_p<NNNN>.json  (فقط پس از اعتبارسنجی کامل نوشته می‌شود)."""
import io
import re
from pathlib import Path

from ..client import LLMInvalidResponse
from ..constants import PROMPT_VERSION, SCHEMA_VERSION
from ..figures import save_figures
from ..prompts import load_prompt
from ..client import request_counter_for
from ..runner import run_items
from ..schemas import validate_page_payload
from ..utils import write_json

TASK = "extract_page"


def pdf_key_from_path(pdf_path):
    stem = Path(pdf_path).stem
    return re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_") or "pdf"


def page_file(project_dir, pdf_key, page):
    return Path(project_dir) / "pages" / f"{pdf_key}_p{page:04d}.json"


def _single_page_bytes(reader, page):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_page(reader.pages[page - 1])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _schema_or_none():
    try:
        from ..schemas import build_pydantic_models
        return build_pydantic_models()
    except Exception:  # pydantic نصب نیست ← حالت JSON + اعتبارسنجی خودمان
        return None


def extract_pages(*, pdf_path, pages, project_dir, client, model, state, log=print, delay=3,
                  stop_event=None, sleep=None, render_scale=2.0, max_consecutive_rate_limits=2,
                  pdf_key=None):
    from pypdf import PdfReader
    import time
    sleep = sleep or time.sleep
    pdf_key = pdf_key or pdf_key_from_path(pdf_path)
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    valid = []
    for p in pages:
        if 1 <= p <= total:
            valid.append(p)
        else:
            log(f"[WARN] صفحه {p} در PDF نیست (کل صفحات: {total}). رد شد.")
    system_prompt = load_prompt("extract")
    schema = _schema_or_none()
    fig_dir = Path(project_dir) / "figures"

    def work(key):
        page = int(key.split(":")[-1])
        data = client.generate_json(
            task=TASK, system_prompt=system_prompt, parts=[("pdf", _single_page_bytes(reader, page))],
            schema=schema, validator=validate_page_payload, model=model)
        data = validate_page_payload(data)  # دفاع دوم: هرگز نامعتبر روی دیسک نمی‌نویسیم
        data["figures"] = save_figures(pdf_path, pdf_key, page, data["figures"], fig_dir, render_scale)
        record = {"pdf": pdf_key, "page": page, **data,
                  "meta": {"model": model, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION}}
        write_json(page_file(project_dir, pdf_key, page), record)
        log(f"[SUCCESS] صفحه {page}: {len(data['questions'])} سوال، {len(data['answers'])} پاسخ، "
            f"{len(data['figures'])} شکل ({data['page_role']}).")

    keys = [f"{pdf_key}:{p}" for p in valid]
    summary = run_items(keys, work, state, "extract", log=log, delay=delay, stop_event=stop_event,
                        sleep=sleep, max_consecutive_rate_limits=max_consecutive_rate_limits,
                        request_counter=request_counter_for(client))
    return summary
