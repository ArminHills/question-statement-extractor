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
from .utils import is_valid_name_component, parse_pages_input, read_json

COMMANDS = ("extract", "assemble", "classify", "atomize", "validate", "export", "run-all", "build-taxonomy",
            "import-review", "merge", "evaluate")
KNOWN_STAGES = ("extract", "assemble", "classify", "atomize", "validate", "export")


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
    p.add_argument("--taxonomy", help="مسیر taxonomy/biology.json (پیش‌فرض: کنار برنامه)")
    p.add_argument("--book-id", help="build-taxonomy: شناسه‌ی کتاب، مثل bio10")
    p.add_argument("--grade", type=int, help="build-taxonomy: پایه (10، 11 یا 12)")
    p.add_argument("--book-title", help="build-taxonomy: عنوان کتاب")
    p.add_argument("--force", action="store_true", help="build-taxonomy: جایگزینی کتاب موجود")
    p.add_argument("--sqlite", action="store_true", help="export: bank.sqlite هم بساز")
    p.add_argument("--gold", help="evaluate: پوشه‌ی gold/*.jsonl (پیش‌فرض: gold/ کنار برنامه)")
    p.add_argument("--review-csv", help="import-review: مسیر review_queue.csv ویرایش‌شده")
    p.add_argument("--from", dest="sources", nargs="+", help="merge: پوشه‌ی پروژه‌هایی که ادغام می‌شوند")
    return p


def _make_manifest(args, pdf_key):
    if args.manifest:
        return Manifest.from_yaml(args.manifest, default_pdf=pdf_key)
    exam_id = args.exam_id or args.name
    return Manifest.single(exam_id, args.year, args.group, default_pdf=pdf_key)


def _make_client(args, cfg, cache, client_factory, log, model):
    if client_factory:
        return client_factory(cfg, cache)
    from .keys import get_api_key_cli
    from .client import GeminiClient
    key = get_api_key_cli()
    if not key:
        log("[FATAL] کلید API پیدا نشد. GEMINI_API_KEY را در محیط یا فایل .env بگذارید.")
        return None
    return CachedClient(GeminiClient(key, model, log=log), cache, model)


def run_pipeline(args, *, client_factory=None, log=print, stop_event=None, sleep=None):
    """اجرای مراحل. client_factory(cfg, cache) -> LLMClient برای تست با Mock. خروجی: کد خروج."""
    cfg = load_config(args.config)
    legacy = args.command is None
    command = args.command or "extract"
    if command == "run-all":
        stages = [s.strip() for s in (args.stages or ",".join(KNOWN_STAGES)).split(",") if s.strip()]
        bad = [s for s in stages if s not in KNOWN_STAGES]
        if bad:
            log(f"[FATAL] مرحله‌ی ناشناخته یا هنوز پیاده‌نشده: {bad}. مرحله‌های موجود: {list(KNOWN_STAGES)}")
            return 2
        stages = [s for s in KNOWN_STAGES if s in stages]  # ترتیب ثابت
    else:
        stages = [command]

    project_dir = Path(args.output_dir) / args.name
    project_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(project_dir / "state.json")
    pdf_key = pdf_key_from_path(args.pdf) if args.pdf else None
    cache = DiskCache(Path(args.output_dir) / ".llm_cache")
    delay = cfg["request_delay_seconds"]
    if cfg.get("batch_size", 1) != 1:
        log("[WARN] batch_size غیر از 1 هنوز پیاده‌سازی نشده؛ یک سوال در هر فراخوانی اجرا می‌شود.")
    skw = dict(sleep=sleep) if sleep else {}
    common = dict(log=log, delay=delay, stop_event=stop_event,
                  max_consecutive_rate_limits=cfg["max_consecutive_rate_limits"], **skw)
    ok = True

    def stopped(summary, name):
        log(f"[INFO] {name}: {len(summary.done)} انجام، {len(summary.skipped)} از قبل انجام‌شده، "
            f"{len(summary.failed)} ناموفق، {len(summary.not_run)} اجرانشده."
            + (f" علت توقف: {summary.stopped_reason}" if summary.stopped_reason else ""))
        return bool(summary.stopped_reason)

    if command == "build-taxonomy":
        if not (args.pdf and args.pages and args.book_id and args.grade and args.book_title):
            log("[FATAL] build-taxonomy به -i، -p، --book-id، --grade و --book-title نیاز دارد.")
            return 2
        from .stages.taxonomy_build import run_build_taxonomy
        model = args.model or cfg["models"]["extract"]
        client = _make_client(args, cfg, cache, client_factory, log, model)
        if client is None:
            return 1
        from .taxonomy import TaxonomyInvalid
        try:
            summary, tax = run_build_taxonomy(
                pdf_path=args.pdf, pages=parse_pages_input(args.pages), project_dir=project_dir, client=client,
                model=model, state=state, book_id=args.book_id, title=args.book_title, grade=args.grade,
                taxonomy_path=args.taxonomy, force=args.force, **common)
        except TaxonomyInvalid as e:
            log(f"[FATAL] {e}")
            return 1
        stopped(summary, "build-taxonomy")
        return 0 if (summary.ok and tax) else 1

    if command == "import-review":
        return _import_review(args, project_dir, log)
    if command == "merge":
        return _merge(args, project_dir, cfg, log)
    if command == "evaluate":
        return _evaluate(args, project_dir, log)

    if args.restart:
        for st in stages:
            state.reset_stage(st)
        log("[INFO] --restart: وضعیت مرحله‌ها پاک شد (پاسخ‌های کش‌شده دوباره مصرف سهمیه نمی‌کنند).")

    if "extract" in stages:
        if not args.pdf or not args.pages:
            log("[FATAL] برای extract گزینه‌های -i و -p لازم است.")
            return 2
        model = args.model or cfg["models"]["extract"]
        client = _make_client(args, cfg, cache, client_factory, log, model)
        if client is None:
            return 1
        summary = extract_pages(pdf_path=args.pdf, pages=parse_pages_input(args.pages), project_dir=project_dir,
                                client=client, model=model, state=state, render_scale=cfg["render_scale"], **common)
        ok = ok and summary.ok
        if stopped(summary, "extract"):
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
    if "classify" in stages:
        from .stages.classify import run_classify
        from .taxonomy import TaxonomyInvalid, TaxonomyMissing, load_taxonomy
        if not (project_dir / "assembled.jsonl").exists():
            log("[FATAL] assembled.jsonl نیست؛ ابتدا assemble را اجرا کنید.")
            return 1
        try:
            tax = load_taxonomy(args.taxonomy)
        except (TaxonomyMissing, TaxonomyInvalid) as e:
            log(f"[FATAL] {e}")
            return 1
        model = cfg["models"]["classify"]
        client = _make_client(args, cfg, cache, client_factory, log, model)
        if client is None:
            return 1
        summary = run_classify(project_dir=project_dir, client=client, model=model, state=state, taxonomy=tax, **common)
        ok = ok and summary.ok
        if stopped(summary, "classify"):
            return 1
    if "atomize" in stages:
        from .stages.atomize import run_atomize
        if not (project_dir / "classified.jsonl").exists():
            log("[FATAL] classified.jsonl نیست؛ ابتدا classify را اجرا کنید.")
            return 1
        model = cfg["models"]["atomize"]
        client = _make_client(args, cfg, cache, client_factory, log, model)
        if client is None:
            return 1
        summary = run_atomize(project_dir=project_dir, client=client, model=model, state=state, **common)
        ok = ok and summary.ok
        if stopped(summary, "atomize"):
            return 1
        rep = read_json(project_dir / "atomize_report.json") or {}
        log(f"[INFO] atomize: {rep.get('statements', 0)} گزاره ({rep.get('statements_null_truth', 0)} با truth=null، "
            f"{rep.get('statements_needs_review', 0)} نیازمند بازبینی)، {rep.get('problems', 0)} مسئله.")
    if "validate" in stages:
        from .stages.validate import validate_project
        from .taxonomy import DEFAULT_PATH, TaxonomyInvalid, TaxonomyMissing, load_taxonomy
        tax = None
        if args.taxonomy or DEFAULT_PATH.exists():
            try:
                tax = load_taxonomy(args.taxonomy)
            except (TaxonomyMissing, TaxonomyInvalid) as e:
                log(f"[FATAL] {e}")
                return 1
        else:
            log("[WARN] taxonomy پیدا نشد؛ اعتبارسنجی topic_ids انجام نمی‌شود.")
        try:
            rep = validate_project(project_dir, tax, log=log)
        except RuntimeError as e:
            log(f"[FATAL] {e}")
            return 1
        log(f"[INFO] validate: {rep['statements']['valid']}/{rep['statements']['in']} گزاره، "
            f"{rep['problems']['valid']}/{rep['problems']['in']} مسئله، {rep['questions']['valid']}/{rep['questions']['in']} سوال معتبر؛ "
            f"{rep['rejected']} رد شد (rejected_invalid.jsonl).")
        ok = ok and rep["rejected"] == 0
    if "export" in stages:
        from .stages.export import export_project
        try:
            export_project(project_dir, similarity_threshold=cfg["similarity_threshold"], sqlite=args.sqlite, log=log)
        except RuntimeError as e:
            log(f"[FATAL] {e}")
            return 1
    return 0 if ok else 1


def _import_review(args, project_dir, log):
    from .review import build_edits_from_rows, load_edits, merge_edits, read_review_csv, record_id, save_edits
    from .utils import read_jsonl
    if not args.review_csv or not os.path.isfile(args.review_csv):
        log("[FATAL] import-review به --review-csv (فایل موجود) نیاز دارد.")
        return 2
    base = project_dir / "validated"
    if not (base / "statements.jsonl").exists():
        log("[FATAL] validated/ نیست؛ ابتدا validate را اجرا کنید.")
        return 1
    recs = {record_id(r): r for f in ("statements", "problems") for r in read_jsonl(base / f"{f}.jsonl")}
    new, errors, ignored = build_edits_from_rows(read_review_csv(args.review_csv), recs)
    for e in errors:
        log(f"[ERROR] {e}")
    if new:
        save_edits(project_dir, merge_edits(load_edits(project_dir), new))
    log(f"[INFO] import-review: {len(new)} ویرایش ذخیره شد، {len(errors)} خطا، {ignored} ردیف اطلاعاتی نادیده گرفته شد. "
        "برای اعمال: python -m exambank export ...")
    return 1 if errors else 0


def _evaluate(args, project_dir, log):
    from .evaluate import run_evaluate
    gold = args.gold or str(Path(__file__).resolve().parent.parent / "gold")
    try:
        run_evaluate(project_dir, gold, log=log)
    except (RuntimeError, FileNotFoundError) as e:
        log(f"[FATAL] {e}")
        return 1
    return 0


def _merge(args, project_dir, cfg, log):
    from .merge import merge_projects
    if not args.sources:
        log("[FATAL] merge به --from <پوشه‌ی پروژه> ... نیاز دارد.")
        return 2
    try:
        merge_projects(project_dir, args.sources, similarity_threshold=cfg["similarity_threshold"],
                       sqlite=args.sqlite, log=log)
    except RuntimeError as e:
        log(f"[FATAL] {e}")
        return 1
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None and not (args.pdf and args.pages):
        parser.error("شکل قدیمی (بدون زیردستور) به -i و -p هم نیاز دارد؛ یا یک زیردستور بدهید (مثلاً export)")
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
    if args.command is None:   # خط پایانی شکل قدیمی (سازگاری)
        word = os.path.join(args.output_dir, args.name, f"{args.name}.docx")
        print(("[DONE] Finished. Word file: " if result["code"] == 0 else
               "[DONE] Finished with errors or was stopped. See the log above. Word file: ") + word, flush=True)
    return result["code"]
