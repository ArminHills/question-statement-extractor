# -*- coding: utf-8 -*-
"""CLI. دو شکل:
  python -m exambank extract|assemble|run-all [گزینه‌ها]
  python -m exambank -n NAME -i FILE -p PAGES -o DIR [-m MODEL] [--restart]   ← شکل قدیمی، سازگار
شکل قدیمی = extract + خروجی docx (مثل قبل)."""
import argparse
import os
import sys
import threading
import traceback
from pathlib import Path

from .assemble import Manifest
from .cache import DiskCache
from .client import CachedClient
from .config import load_config
from .state import StateStore
from .stages.assemble import load_pages, run_assemble
from .stages.extract import extract_pages, pdf_key_from_path
from .utils import is_valid_name_component, parse_pages_input

COMMANDS = ("extract", "assemble", "run-all")
KNOWN_STAGES = ("extract", "assemble")  # بقیه‌ی مرحله‌ها در milestoneهای بعد اضافه می‌شوند


def build_parser():
    p = argparse.ArgumentParser(
        prog="exambank",
        description="استخراج سوالات کنکور از PDF با Gemini. بدون زیردستور = شکل قدیمی (extract + docx).",
        epilog='مثال: python -m exambank -n Bio10.3 -i book.pdf -p "250-255, 260" -o D:\\Projects')
    p.add_argument("command", nargs="?", choices=COMMANDS, help="مرحله (پیش‌فرض: شکل قدیمی)")
    p.add_argument("-n", "--name", required=True, help="نام پروژه")
    p.add_argument("-i", "--pdf", help="فایل PDF")
    p.add_argument("-p", "--pages", help='صفحه‌ها، مثل "250-255, 260"')
    p.add_argument("-o", "--output-dir", required=True)
    p.add_argument("-m", "--model", help="مدل مرحله‌ی extract (پیش‌فرض از config.yaml)")
    p.add_argument("--restart", action="store_true", help="state مرحله‌ی extract را پاک کن (کش پابرجاست)")
    p.add_argument("--config", help="مسیر config.yaml")
    p.add_argument("--layout", choices=("auto", "interleaved", "separate"), default="auto")
    p.add_argument("--manifest", help="فایل yaml نگاشت صفحه‌ها به آزمون")
    p.add_argument("--exam-id")
    p.add_argument("--year", type=int, help="سال شمسی آزمون")
    p.add_argument("--group", help="گروه آزمون")
    p.add_argument("--stages", help="مراحل run-all، با ویرگول: extract,assemble")
    p.add_argument("--docx", action="store_true", help="خروجی docx خواندنی هم بساز")
    return p


def _make_manifest(args, pdf_key):
    if args.manifest:
        return Manifest.from_yaml(args.manifest, default_pdf=pdf_key)
    exam_id = args.exam_id or args.name
    return Manifest.single(exam_id, args.year, args.group, default_pdf=pdf_key)


def run_pipeline(args, *, client_factory=None, log=print, stop_event=None, sleep=None):
    """اجرای مراحل. client_factory(cfg, cache) -> LLMClient برای تست با Mock. خروجی: کد خروج."""
    cfg = load_config(args.config)
    legacy = args.command is None
    command = args.command or "extract"
    if command == "run-all":
        stages = [s.strip() for s in (args.stages or "extract,assemble").split(",") if s.strip()]
        bad = [s for s in stages if s not in KNOWN_STAGES]
        if bad:
            log(f"[FATAL] مرحله‌ی ناشناخته یا هنوز پیاده‌نشده: {bad}. مرحله‌های موجود: {list(KNOWN_STAGES)}")
            return 2
    else:
        stages = [command]

    project_dir = Path(args.output_dir) / args.name
    project_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(project_dir / "state.json")
    pdf_key = pdf_key_from_path(args.pdf) if args.pdf else None
    ok = True

    if "extract" in stages:
        if not args.pdf or not args.pages:
            log("[FATAL] برای extract گزینه‌های -i و -p لازم است.")
            return 2
        pages = parse_pages_input(args.pages)
        model = args.model or cfg["models"]["extract"]
        cache = DiskCache(Path(args.output_dir) / ".llm_cache")
        if client_factory:
            client = client_factory(cfg, cache)
        else:
            from .keys import get_api_key_cli
            from .client import GeminiClient
            key = get_api_key_cli()
            if not key:
                log("[FATAL] کلید API پیدا نشد. GEMINI_API_KEY را در محیط یا فایل .env بگذارید.")
                return 1
            client = CachedClient(GeminiClient(key, model, log=log), cache, model)
        if args.restart:
            state.reset_stage("extract")
            log("[INFO] --restart: وضعیت extract پاک شد (پاسخ‌های کش‌شده دوباره مصرف سهمیه نمی‌کنند).")
        kwargs = dict(sleep=sleep) if sleep else {}
        summary = extract_pages(
            pdf_path=args.pdf, pages=pages, project_dir=project_dir, client=client, model=model, state=state,
            log=log, delay=cfg["request_delay_seconds"], stop_event=stop_event,
            render_scale=cfg["render_scale"], max_consecutive_rate_limits=cfg["max_consecutive_rate_limits"],
            **kwargs)
        log(f"[INFO] extract: {len(summary.done)} انجام، {len(summary.skipped)} از قبل انجام‌شده، "
            f"{len(summary.failed)} ناموفق، {len(summary.not_run)} اجرانشده."
            + (f" علت توقف: {summary.stopped_reason}" if summary.stopped_reason else ""))
        ok = ok and summary.ok
        if summary.stopped_reason:
            return 1  # با پاسخ ناقص مراحل بعد را اجرا نمی‌کنیم
    if (args.docx or legacy) and "extract" in stages:
        from .docx_out import write_docx
        recs = load_pages(project_dir, pdf_key)
        out = project_dir / f"{args.name}.docx"
        try:
            write_docx(out, args.name, os.path.basename(args.pdf), recs)
            log(f"[INFO] فایل Word: {out}")
        except Exception as e:  # noqa: BLE001
            log(f"[ERROR] ذخیره‌ی docx ناموفق: {e}")
            ok = False
    if "assemble" in stages:
        manifest = _make_manifest(args, pdf_key)
        try:
            records, issues = run_assemble(project_dir, manifest, args.layout,
                                           tuple(cfg["expected_option_counts"]), pdf_key)
        except RuntimeError as e:
            log(f"[FATAL] {e}")
            return 1
        log(f"[INFO] assemble: {len(records)} سوال، {len(issues)} مورد در assemble_report.json")
    return 0 if ok else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not is_valid_name_component(args.name.strip()):
        parser.error('نام پروژه نامعتبر است (< > : " / \\ | ? *)')
    args.name = args.name.strip()
    if args.pdf and not os.path.isfile(args.pdf):
        parser.error(f"PDF پیدا نشد: {args.pdf}")
    if not os.path.isdir(args.output_dir):
        parser.error(f"پوشه‌ی خروجی وجود ندارد: {args.output_dir}")
    if args.pages:
        try:
            parse_pages_input(args.pages)
        except Exception:  # noqa: BLE001
            parser.error('قالب صفحه‌ها نادرست است. نمونه: "250-255, 260, 270-275"')

    stop_event = threading.Event()
    result = {"code": 1}

    def worker():
        try:
            result["code"] = run_pipeline(args, log=lambda m: print(m, flush=True), stop_event=stop_event)
        except Exception:  # noqa: BLE001
            print(f"[FATAL] خطای غیرمنتظره:\n{traceback.format_exc()}", flush=True)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.2)
    except KeyboardInterrupt:
        stop_event.set()
        print("\n[INFO] توقف درخواست شد؛ پس از پایان صفحه‌ی فعلی متوقف می‌شود (Ctrl+C دوباره = خروج فوری)...")
        try:
            while t.is_alive():
                t.join(0.2)
        except KeyboardInterrupt:
            return 130
    return result["code"]
