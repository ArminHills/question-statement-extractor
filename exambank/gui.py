# -*- coding: utf-8 -*-
"""GUI (Tkinter): همان رابط قبلی + حداقل گسترش (انتخاب مراحل، چیدمان، manifest، شناسه/سال/گروه آزمون، taxonomy، docx/sqlite).
منطق تبدیل فرم به آرگومان CLI در form_to_argv است (خالص و تست‌شده)؛ کد ویجت‌ها در محیط توسعه اجرا نشده (tkinter نبود)."""
import os
import queue
import sys
import threading
import traceback

from .cli import KNOWN_STAGES, build_parser, run_pipeline
from .config import load_config
from .utils import is_valid_name_component, parse_pages_input

try:  # در سرورهای بدون tkinter فقط form_to_argv قابل‌استفاده است
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover
    tk = None

STAGE_LABELS = {"extract": "استخراج (Gemini)", "assemble": "سوارکردن و اتصال پاسخ", "classify": "طبقه‌بندی (Gemini)",
                "atomize": "گزاره‌ها و مسئله‌ها (Gemini)", "validate": "اعتبارسنجی", "export": "خروجی نهایی"}
LLM_STAGES = ("extract", "classify", "atomize")


def form_to_argv(v):
    """v: دیکشنری فرم. -> لیست آرگومان CLI. خطای فرم = ValueError با پیام فارسی."""
    stages = [s for s in KNOWN_STAGES if s in (v.get("stages") or [])]
    if not stages:
        raise ValueError("حداقل یک مرحله را انتخاب کن.")
    name = (v.get("name") or "").strip()
    if not name or not is_valid_name_component(name):
        raise ValueError('نام پروژه نامعتبر است (نباید شامل < > : " / \\ | ? * باشد).')
    out = (v.get("output_dir") or "").strip()
    if not out or not os.path.isdir(out):
        raise ValueError("لطفاً پوشه ذخیره‌سازی را انتخاب کن.")
    argv = ["extract" if stages == ["extract"] else "run-all", "-n", name, "-o", out]
    if stages != ["extract"]:
        argv += ["--stages", ",".join(stages)]
    if "extract" in stages:
        pdf = (v.get("pdf_path") or "").strip()
        if not pdf or not os.path.isfile(pdf):
            raise ValueError("لطفاً یک فایل PDF معتبر انتخاب کن.")
        try:
            parse_pages_input((v.get("pages") or "").strip())
        except Exception:
            raise ValueError("فرمت محدوده صفحات نادرست است.\nنمونه صحیح: 250-255, 260, 270-275")
        argv += ["-i", pdf, "-p", v["pages"].strip()]
    elif (v.get("pdf_path") or "").strip():
        argv += ["-i", v["pdf_path"].strip()]
    if (v.get("model") or "").strip():
        argv += ["-m", v["model"].strip()]
    if v.get("layout") and v["layout"] != "auto":
        argv += ["--layout", v["layout"]]
    for key, flag in (("manifest", "--manifest"), ("exam_id", "--exam-id"), ("group", "--group"), ("taxonomy", "--taxonomy")):
        if (v.get(key) or "").strip():
            argv += [flag, v[key].strip()]
    year = (v.get("year") or "").strip()
    if year:
        if not year.isdigit():
            raise ValueError("سال شمسی باید عدد باشد (مثلاً 1403).")
        argv += ["--year", year]
    for key, flag in (("docx", "--docx"), ("sqlite", "--sqlite"), ("restart", "--restart")):
        if v.get(key):
            argv.append(flag)
    return argv


def needs_api_key(argv):
    args = build_parser().parse_args(argv)
    stages = [s for s in (args.stages or "").split(",") if s] if args.command == "run-all" else [args.command]
    return any(s in LLM_STAGES for s in stages)


class ExtractionApp:
    def __init__(self, root):
        self.root = root
        root.title("استخراج و ساخت بانک گزاره‌ی کنکور — Gemini")
        root.geometry("820x860")
        root.minsize(720, 700)
        root.option_add("*Font", ("Tahoma", 10))
        cfg = load_config()
        S = tk.StringVar
        self.vars = {k: S() for k in ("pdf_path", "name", "pages", "output_dir", "manifest", "exam_id", "year", "group", "taxonomy")}
        self.vars["model"] = S(value=cfg["models"]["extract"])
        self.vars["layout"] = S(value="auto")
        self.flags = {"docx": tk.BooleanVar(value=True), "sqlite": tk.BooleanVar(value=False), "restart": tk.BooleanVar(value=False)}
        self.stage_vars = {s: tk.BooleanVar(value=(s == "extract")) for s in KNOWN_STAGES}
        self.status_var = S(value="آماده")
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.last_project_folder = None
        self.total_pages = None
        self.done_pages = 0
        self._build_ui()
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------ UI
    def _entry_row(self, parent, row, label, key, browse=None, readonly=False):
        pad = {"padx": 8, "pady": 4}
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", **pad)
        ttk.Entry(parent, textvariable=self.vars[key], justify="right", state="readonly" if readonly else "normal").grid(
            row=row, column=1, sticky="ew", **pad)
        if browse:
            ttk.Button(parent, text="انتخاب...", command=browse).grid(row=row, column=2, **pad)

    def _build_ui(self):
        form = ttk.LabelFrame(self.root, text="تنظیمات پروژه")
        form.pack(fill="x", padx=10, pady=(10, 4))
        form.columnconfigure(1, weight=1)
        self._entry_row(form, 0, "فایل PDF ورودی:", "pdf_path", self.on_browse_pdf, True)
        self._entry_row(form, 1, "نام پروژه (مثلاً Bio10.3):", "name")
        self._entry_row(form, 2, "محدوده صفحات (مثلاً 250-255, 260):", "pages")
        self._entry_row(form, 3, "پوشه ذخیره‌سازی:", "output_dir", self.on_browse_dir, True)
        ttk.Label(form, text="مدل Gemini (مرحله‌ی استخراج):").grid(row=4, column=0, sticky="e", padx=8, pady=4)
        ttk.Combobox(form, textvariable=self.vars["model"], justify="right",
                     values=["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]).grid(row=4, column=1, columnspan=2, sticky="ew", padx=8, pady=4)

        st = ttk.LabelFrame(self.root, text="مراحل اجرا (پیش‌فرض مثل قبل: فقط استخراج + Word)")
        st.pack(fill="x", padx=10, pady=4)
        for i, s in enumerate(KNOWN_STAGES):
            ttk.Checkbutton(st, text=STAGE_LABELS[s], variable=self.stage_vars[s]).grid(row=i // 3, column=i % 3, sticky="w", padx=10, pady=2)

        adv = ttk.LabelFrame(self.root, text="اطلاعات آزمون و خروجی")
        adv.pack(fill="x", padx=10, pady=4)
        adv.columnconfigure(1, weight=1)
        self._entry_row(adv, 0, "شناسه‌ی آزمون (exam-id):", "exam_id")
        self._entry_row(adv, 1, "سال شمسی:", "year")
        self._entry_row(adv, 2, "گروه آزمون:", "group")
        self._entry_row(adv, 3, "فایل manifest (اختیاری):", "manifest", lambda: self._pick("manifest", [("YAML", "*.yaml *.yml")]), True)
        self._entry_row(adv, 4, "فایل taxonomy (اختیاری):", "taxonomy", lambda: self._pick("taxonomy", [("JSON", "*.json")]), True)
        ttk.Label(adv, text="چیدمان پاسخ‌ها:").grid(row=5, column=0, sticky="e", padx=8, pady=4)
        ttk.Combobox(adv, textvariable=self.vars["layout"], values=["auto", "interleaved", "separate"], state="readonly",
                     justify="right").grid(row=5, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        opts = ttk.Frame(adv)
        opts.grid(row=6, column=0, columnspan=3, sticky="e", padx=8, pady=4)
        ttk.Checkbutton(opts, text="فایل Word هم بساز", variable=self.flags["docx"]).pack(side="right", padx=8)
        ttk.Checkbutton(opts, text="bank.sqlite هم بساز", variable=self.flags["sqlite"]).pack(side="right", padx=8)
        ttk.Checkbutton(opts, text="شروع از نو (--restart؛ کش سهمیه مصرف نمی‌کند)", variable=self.flags["restart"]).pack(side="right", padx=8)

        bf = ttk.Frame(self.root)
        bf.pack(fill="x", padx=10, pady=(4, 4))
        self.start_btn = ttk.Button(bf, text="شروع", command=self.on_start)
        self.start_btn.pack(side="right", padx=4)
        self.stop_btn = ttk.Button(bf, text="توقف", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="right", padx=4)
        self.open_btn = ttk.Button(bf, text="باز کردن پوشه خروجی", command=self.on_open_folder, state="disabled")
        self.open_btn.pack(side="right", padx=4)
        pf = ttk.Frame(self.root)
        pf.pack(fill="x", padx=10, pady=(0, 4))
        self.progress = ttk.Progressbar(pf, mode="determinate")
        self.progress.pack(fill="x", side="left", expand=True, padx=(0, 8))
        ttk.Label(pf, textvariable=self.status_var, width=18).pack(side="right")
        lf = ttk.LabelFrame(self.root, text="گزارش زنده")
        lf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(lf, wrap="word", height=12, font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------- actions
    def _pick(self, key, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            self.vars[key].set(p)

    def on_browse_pdf(self):
        p = filedialog.askopenfilename(title="فایل PDF اصلی را انتخاب کنید", filetypes=[("PDF Files", "*.pdf")])
        if p:
            self.vars["pdf_path"].set(p)

    def on_browse_dir(self):
        d = filedialog.askdirectory(title="پوشه محل ذخیره پروژه را انتخاب کنید")
        if d:
            self.vars["output_dir"].set(d)

    def on_open_folder(self):
        if self.last_project_folder and os.path.isdir(self.last_project_folder):
            try:
                if sys.platform == "win32":
                    os.startfile(self.last_project_folder)
                else:
                    messagebox.showinfo("مسیر پوشه", self.last_project_folder)
            except Exception as e:  # noqa: BLE001
                messagebox.showwarning("خطا", f"باز کردن پوشه ناموفق بود: {e}")

    def _append_log(self, msg):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _load_or_prompt_api_key(self):
        from dotenv import load_dotenv, set_key
        from .keys import ENV_PATH
        load_dotenv(ENV_PATH)
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if key:
            return key
        key = simpledialog.askstring("کلید Gemini API", "کلید Gemini API را وارد کنید.\n(از aistudio.google.com/apikey قابل دریافت است):",
                                     show="*", parent=self.root)
        if not key:
            return None
        key = key.strip()
        if messagebox.askyesno("ذخیره کلید", "این کلید در فایل .env کنار برنامه ذخیره شود؟\n(این فایل را با کسی به اشتراک نگذار)"):
            try:
                if not ENV_PATH.exists():
                    ENV_PATH.touch()
                set_key(str(ENV_PATH), "GEMINI_API_KEY", key)
            except Exception as e:  # noqa: BLE001
                messagebox.showwarning("هشدار", f"ذخیره کلید در .env ناموفق بود: {e}")
        os.environ["GEMINI_API_KEY"] = key
        return key

    def on_start(self):
        values = {k: v.get() for k, v in self.vars.items()}
        values.update({k: v.get() for k, v in self.flags.items()})
        values["stages"] = [s for s, v in self.stage_vars.items() if v.get()]
        try:
            argv = form_to_argv(values)
        except ValueError as e:
            messagebox.showerror("خطا", str(e))
            return
        api_key = None
        if needs_api_key(argv):
            api_key = self._load_or_prompt_api_key()
            if not api_key:
                return
        args = build_parser().parse_args(argv)
        self.last_project_folder = os.path.join(args.output_dir, args.name)
        self.log_text.delete("1.0", "end")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.open_btn.config(state="normal")
        self.status_var.set("در حال پردازش...")
        self.stop_event.clear()
        self.done_pages = 0
        try:
            self.total_pages = len(parse_pages_input(args.pages)) if args.pages and (args.command == "extract" or "extract" in (args.stages or "")) else None
        except Exception:  # noqa: BLE001
            self.total_pages = None
        self.progress.config(mode="determinate" if self.total_pages else "indeterminate", maximum=self.total_pages or 100, value=0)
        if not self.total_pages:
            self.progress.start(80)
        threading.Thread(target=self._run_worker, args=(args, api_key, values["model"]), daemon=True).start()

    def on_stop(self):
        self.stop_event.set()
        self._append_log("[INFO] درخواست توقف ثبت شد؛ پس از پایان صفحه/سوال فعلی، پردازش متوقف می‌شود...")
        self.stop_btn.config(state="disabled")

    def _run_worker(self, args, api_key, model):
        code = 1
        factory = None
        if api_key:
            from .cache import DiskCache  # noqa: F401
            from .client import CachedClient, GeminiClient
            factory = lambda cfg, cache: CachedClient(  # noqa: E731
                GeminiClient(api_key, model, log=lambda m: self.log_queue.put(("log", m))), cache, model)
        try:
            code = run_pipeline(args, client_factory=factory, log=lambda m: self.log_queue.put(("log", m)),
                                stop_event=self.stop_event)
        except Exception:  # noqa: BLE001
            self.log_queue.put(("log", f"[FATAL] خطای غیرمنتظره:\n{traceback.format_exc()}"))
        finally:
            self.log_queue.put(("done", code))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                    if self.total_pages and payload.startswith("[SUCCESS] صفحه"):
                        self.done_pages += 1
                        self.progress["value"] = self.done_pages
                        self.status_var.set(f"{self.done_pages} از {self.total_pages} صفحه")
                elif kind == "done":
                    self.progress.stop()
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    if payload == 0:
                        self.status_var.set("پایان یافت")
                        messagebox.showinfo("موفقیت", f"پردازش تمام شد.\nپوشه‌ی پروژه:\n{self.last_project_folder}")
                    else:
                        self.status_var.set("پایان با خطا")
                        messagebox.showwarning("پایان با خطا", "پردازش با خطا تمام شد یا متوقف شد. جزئیات را در گزارش زنده ببین.\n"
                                               "(در صورت اتمام سهمیه، وضعیت ذخیره شده و فردا ادامه می‌یابد.)")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)


def main():
    if tk is None:
        raise SystemExit("tkinter نصب نیست؛ از خط فرمان استفاده کنید: python -m exambank -h")
    root = tk.Tk()
    ExtractionApp(root)
    root.mainloop()
