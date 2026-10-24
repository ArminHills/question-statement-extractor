# -*- coding: utf-8 -*-
"""تولید SCHEMA.md و schema/*.schema.json از روی records.py (منبع حقیقت واحد)."""
import json
from pathlib import Path

from .constants import SCHEMA_VERSION
from .records import (Dedupe, Exam, Pages, Problem, Parametrization, Question, ReviewInfo, Slot, Source, Statement,
                      _fields, json_schema, type_repr)

REASON_CATALOG = {
    # truth_rules
    "answer_status_annulled": "سوال حذف‌شده؛ truth همیشه null",
    "answer_status_disputed": "سوال مورد اعتراض؛ truth همیشه null",
    "no_key": "کلیدی برای سوال نیست (و پاسخ تشریحی هم حکم صریح نداده)",
    "key_not_in_options": "کلید جزو گزینه‌ها نیست (خطای استخراج یا برچسب‌گذاری)",
    "polarity_missing": "polarity صورت سوال مشخص نیست؛ truth تعیین نشد",
    "multiple_keys_combo": "چند کلید برای سوال «موارد الف..د»؛ ابهام",
    "multiple_keys_count": "چند کلید برای سوال شمارشی؛ ابهام",
    "matching_ambiguous": "گزینه‌های matching/ordering بدون معنای قطعی",
    "combo_sets_missing": "مجموعه‌ی موارد گزینه‌ی کلید قطعی نشد",
    "combo_item_unknown": "گزینه‌ی کلید به مورد ناموجود اشاره می‌کند",
    "combo_kind_unknown": "نوع گزینه‌ی combo نامعتبر",
    "count_missing": "عدد گزینه‌ی کلید خوانده نشد",
    "count_out_of_range": "عدد کلید خارج از ۰..تعداد موارد",
    "count_partial_unresolved": "تعداد میانی؛ بدون حکم صریح پاسخ تشریحی truth تک‌تک موارد نامعلوم است (null)",
    "count_inconsistent_with_explanations": "احکام صریح پاسخ تشریحی با عدد کلید نمی‌خواند",
    "structure_unsupported": "structure پشتیبانی‌نشده برای تعیین truth",
    "conflict_explanation_vs_key": "حکم صریح پاسخ تشریحی با استنتاج از کلید ناسازگار است (مقدار پاسخ تشریحی نگه داشته شده)",
    # atomize
    "evidence_not_found": "evidence حکم صریح عیناً در پاسخ تشریحی نبود؛ حکم نادیده گرفته شد",
    "why_false_not_found": "why_false عیناً در پاسخ تشریحی نبود؛ حذف شد",
    "item_text_not_in_stem": "متن مورد (الف..د) در صورت سوال پیدا نشد",
    "standalone_adds_words": "text_standalone واژه‌ی زیادی خارج از صورت سوال/گزینه دارد (احتمال افزودن واقعیت)",
    "standalone_missing": "LLM text_standalone نداد؛ text_original جایگزین شد",
    "context_ref_invalid": "context_ref گزاره به شکل/سناریوی موجود اشاره نمی‌کند",
    "context_inconsistent": "گزاره وابسته به شکل/سناریو است ولی سوال context_dependency=none دارد",
    "parts_rejected": "تجزیه‌ی گزینه‌ی مرکب رد شد (حکم صریح همه‌ی اجزا تأیید نشد)",
    "llm_statement_missing": "LLM برای این گزینه گزاره نساخت",
    "llm_extra_statement": "LLM گزاره‌ی اضافه برای گزینه‌ی ناموجود ساخت (نادیده گرفته شد)",
    "items_missing": "موارد الف..د از صورت سوال استخراج نشد",
    "option_parse_disagreement": "معنای گزینه‌ی کلید در تحلیل‌گر قاعده‌محور و LLM فرق دارد (تحلیل‌گر برنده است)",
    "option_semantics_llm_only": "معنای گزینه‌ی کلید فقط از LLM آمده و قاعده‌محور تأیید نکرد",
    "given_not_in_stem": "یکی از givens مسئله عیناً در صورت سوال نبود",
    "asked_not_in_stem": "asked مسئله عیناً در صورت سوال نبود",
    "slot_value_not_in_stem": "مقدار slot در صورت مسئله نبود؛ slot حذف شد",
    "parametrization_dropped": "مسئله‌ی واریانت‌پذیر بدون slot معتبر؛ is_candidate=false شد",
    "new_problem_class": "problem_class جدید (خارج از فهرست)",
    # classify
    "polarity_evidence_not_found": "نقل polarity_evidence در صورت سوال نبود",
    "polarity_disagreement": "نشانه‌های واژگانی صورت سوال با polarity ناسازگار است (مثلاً «نادرست» ولی positive)",
    "structure_option_mismatch": "گزینه‌ها با structure اعلام‌شده نمی‌خوانند",
    "route_structure_mismatch": "route با structure ناسازگار است",
    "figure_context_mismatch": "وجود/نبود شکل با context_dependency نمی‌خواند",
    # validate / dedupe / review
    "key_status_inconsistent": "answer_status با خالی/پُر بودن کلید نمی‌خواند",
    "source_missing_year": "سال شمسی آزمون مشخص نیست",
    "source_missing_group": "گروه آزمون مشخص نیست",
    "topic_not_in_taxonomy": "مبحث خارج از taxonomy",
    "asm_unusual_option_count": "تعداد گزینه‌ی سوال غیرعادی (assemble)",
    "asm_duplicate_question_number": "شماره‌ی سوال در آزمون تکراری است",
    "asm_key_not_in_options": "کلید جزو گزینه‌ها نیست (assemble)",
    "asm_key_conflict_between_sources": "کلید جدول و پاسخ تشریحی با هم فرق دارند",
    "asm_stitch_number_mismatch": "شماره‌ی سوال ادامه‌دار با شماره‌ی صفحه‌ی قبل نمی‌خواند؛ سوار نشد",
    "asm_dangling_continuation": "ادامه‌ی سوال پیدا نشد؛ سوال ممکن است ناقص باشد",
    "asm_orphan_continuation": "قطعه‌ی ادامه بدون سوال قبلی",
    "asm_duplicate_option_labels": "برچسب گزینه‌ها تکراری است",
    "cross_year_truth_conflict": "گزاره‌ی مشابه در آزمون دیگر truth متضاد دارد (معمولاً خطای استخراج یا تغییر نسخه‌ی کتاب)",
    "stale_review_edit": "متن رکورد بعد از بازبینی عوض شده؛ ویرایش قبلی اعمال نشد",
}

_STATIC_HEAD = """# SCHEMA.md — قرارداد خروجی (نسخه‌ی {ver})

این فایل خودکار از `exambank/records.py` ساخته می‌شود؛ دستی ویرایش نکنید.
مصرف‌کننده‌ی اصلی: برنامه‌ی Frankenstein (ترکیب گزاره‌ها و ساخت سوال جدید).

## فایل‌ها
| فایل | محتوا |
|---|---|
| `statements.jsonl` | **بانک گزاره‌ها** — هر خط یک `Statement` |
| `problems.jsonl` | **بانک مسئله‌ها** — هر خط یک `Problem` |
| `questions.jsonl` | رکورد کامل سوال‌ها (منبع حقیقت برای ردیابی) — هر خط یک `Question` |
| `statements.csv` | `statements.jsonl` به‌صورت تخت (UTF-8 با BOM برای Excel) |
| `review_queue.csv` | رکوردهای نیازمند بازبینی انسانی |
| `figures/` | تصویر شکل‌ها (`source`ها با `figure_id`/`image_path`) |
| `schema/*.schema.json` | JSON Schema همین قرارداد |
| `bank.sqlite` | اختیاری (`--sqlite`) |
همه‌ی JSONL‌ها UTF-8 با `ensure_ascii=False`، هر خط یک شیء. رکوردهای `review_status=rejected` در خروجی نیستند.
فیلد ناشناخته ممکن است در نسخه‌های بعد اضافه شود؛ مصرف‌کننده باید آن را نادیده بگیرد.

## قواعد مصرف (مهم برای Frankenstein)
1. **فقط `truth` غیر null با `truth_basis` ≠ `unresolved`** مبنای ساخت سوال است. `null` یعنی پاسخنامه آن را مشخص نکرده.
2. **`needs_review=true`** یعنی هنوز بازبینی انسانی نشده و ممکن است اشتباه باشد؛ برای تولید جدی فقط `needs_review=false` یا
   `review_status ∈ {{approved, edited}}` را بردارید. `llm_confidence` فقط کمکی است.
3. **`standalone=false`** یعنی گزاره بدون `context_ref` (شکل یا سناریو) معنا ندارد.
4. **`is_atomic=false`** یعنی گزینه‌ی مرکب است؛ به‌عنوان یک گزاره‌ی واحد درست/غلط است. اجزای تجزیه‌شده با `parent_statement_id` آمده‌اند.
5. **تکرار**: رکوردها پاک نمی‌شوند. `dedupe.canonical_id` نماینده‌ی خوشه و `dedupe.occurrences`/`years` تعداد و سال رخدادهاست (فراوانی بین سال‌ها خودش داده‌ی ارزشمند است).
   برای یکتا کردن، رکوردهای `statement_id == dedupe.canonical_id` را بردارید.
6. `text_original` عین متن PDF است؛ `text_standalone` ساخته‌ی LLM است (کمترین تغییر، بدون «تصحیح» محتوای غلط).

## polarity و truth
- `polarity` فقط برای درخواست ارزیابیِ **صورت سوال** است («نادرست است»، «به‌جز»، «کدام … نیست» ← `negative`). نفیِ داخل خود گزاره جزء متن گزاره است و polarity را عوض نمی‌کند.
- مثال: «کدام عبارت نادرست است؟» با گزینه‌ی «گویچه‌ی قرمز بالغ هسته دارد» (کلید) ← `polarity=negative`؛ گزاره = متن گزینه، `truth=false`.
- مثال: «کدام یاخته فاقد هسته است؟» با گزینه‌ی «گویچه‌ی قرمز بالغ» (کلید) ← `polarity=positive`؛ گزاره = «گویچه‌ی قرمز بالغ فاقد هسته است»، `truth=true`.
- `truth` را **کد** تعیین می‌کند، نه LLM: از کلید (`key_derived`) یا حکم صریح پاسخ تشریحی با نقل عین (`explanation`)؛ در غیر این صورت `null` (`unresolved`).
  `count_items` با عدد میانی بدون حکم صریح ← null. سوال حذف‌شده/معترض ← null. `manual` = تعیین/تأیید بازبین انسانی.

## قرارداد نرمال‌سازی متن
فیلدهای `*_original` عین متن‌اند. فیلدهای نرمال‌شده (`stem`، `options[].text`، `explanation`): ی/ک فارسی، ارقام ASCII، نیم‌فاصله‌ی درست (می‌‌/نمی‌، ‌ها/‌های/‌تر/‌ترین)،
فاصله‌ی تمیز. نمادها: بالا/پایین‌نویسِ فقط-رقم/علامت با یونیکد (`Na⁺`، `Ca²⁺`، `CO₂`، `H₂O`)؛ هر بالا/پایین‌نویس شامل حرف همیشه با آکولاد (`X^{{A}} X^{{a}}`، `I^{{A}}i`).
فرمول/یون فقط از فهرست سفید تبدیل می‌شود.

## گردش بازبینی
`review_queue.csv` (UTF-8 با BOM) را در Excel باز کنید؛ ستون‌های `decision` (approved/edited/rejected)، `truth_new` (true/false/null)،
`text_new`، `note` را پر کنید و `python -m exambank import-review ...` سپس `export`. ویرایش‌ها در `review_edits.jsonl` جدا نگه‌داری می‌شوند و اجرای دوباره‌ی مراحل آن‌ها را بازنویسی نمی‌کند.

## شناسه‌ها
`question_id = <exam_id>_q<NNN>`؛ گزاره‌ی گزینه `<question_id>_o<برچسب>`؛ گزاره‌ی مورد `<question_id>_i<شماره‌ی ترتیبی>`؛ جزء `…_p<k>`؛ مسئله `<question_id>_prob`. پایدار و قابل‌upsert‌اند.
"""


def _table(cls):
    rows = ["| فیلد | نوع | الزامی | توضیح |", "|---|---|---|---|"]
    for name, t in _fields(cls).items():
        req = "خیر" if hasattr(cls, name) else "بله"
        rows.append(f"| `{name}` | {type_repr(t)} | {req} | {cls.DOC.get(name, '')} |")
    return "\n".join(rows)


def render_schema_md():
    parts = [_STATIC_HEAD.format(ver=SCHEMA_VERSION)]
    for title, cls in (("Statement (بانک گزاره‌ها)", Statement), ("Problem (بانک مسئله‌ها)", Problem),
                       ("Question (questions.jsonl)", Question)):
        parts.append(f"## {title}\n\n{cls.__doc__.strip().splitlines()[0]}\n\n{_table(cls)}\n")
    parts.append("## زیرساختارها\n")
    for cls in (Source, Pages, Dedupe, ReviewInfo, Exam, Parametrization, Slot):
        doc = (cls.__doc__ or "").strip().splitlines()
        parts.append(f"### {cls.__name__}\n{doc[0] if doc else ''}\n\n{_table(cls)}\n")
    parts.append("## کدهای review_reasons\n\n| کد | معنی |\n|---|---|")
    for k in sorted(REASON_CATALOG):
        parts.append(f"| `{k}` | {REASON_CATALOG[k]} |")
    return "\n".join(parts) + "\n"


def write_schema_files(project_dir):
    pd = Path(project_dir)
    (pd / "schema").mkdir(parents=True, exist_ok=True)
    for name, cls in (("statement", Statement), ("problem", Problem), ("question", Question)):
        (pd / "schema" / f"{name}.schema.json").write_text(
            json.dumps(json_schema(cls), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (pd / "SCHEMA.md").write_text(render_schema_md(), encoding="utf-8")
