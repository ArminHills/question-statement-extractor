# smoke_test.md — اجرای واقعی روی ۳ صفحه (تا پایان M4)

## ۰) آماده‌سازی
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .        # باید بدون skip سبز باشد
```
کلید: فایل `.env` کنار برنامه با `GEMINI_API_KEY=...`.

## ۱) taxonomy (یک‌بار برای هر کتاب)
صفحه‌های فهرست مطالب کتاب زیست دهم (و سپس یازدهم، دوازدهم) را انتخاب کنید:
```
python -m exambank build-taxonomy -n tax -i fehrest-10.pdf -p "3-4" -o D:\Projects --book-id bio10 --grade 10 --book-title "زیست‌شناسی دهم"
```
خروجی: `taxonomy\biology.json`. **حتماً با کتاب مقایسه کنید** (شماره‌ی فصل/گفتار، عنوان‌ها). برای کتاب بعدی همین دستور با `--book-id bio11 --grade 11 ...`.

## ۲) extract + assemble روی ۳ صفحه‌ی نماینده
شامل: (الف) سوال بریده‌شده بین دو صفحه، (ب) صفحه‌ی دارای شکل، (پ) کلید پاسخ.
```
python -m exambank run-all -n smoke -i exam.pdf -p "41-42, 87" -o D:\Projects --exam-id bio-1403-t --year 1403 --group تجربی --stages extract,assemble
```
بررسی چشمی (مهم‌ترین قدم): `pages\*.json` با PDF؛ ستون‌های جدول کلید؛ `figures\` (برش‌ها درست‌اند یا `full_page_fallback`؟ اگر همیشه fallback یا جابه‌جا بود، `pages\*.json` و `figures\*` را بدهید).

## ۳) classify
```
python -m exambank classify -n smoke -o D:\Projects
```
بررسی: `classified.jsonl` — برای ۵ سوال چک کنید: `structure`، `polarity` و `polarity_evidence` (عبارت واقعاً در صورت سوال هست؟)، `route`، `topic_ids` (منطقی‌اند؟). `review_reasons` را بخوانید؛ `polarity_disagreement` یعنی نشانه‌های واژگانی و polarity مدل نمی‌خوانند.

## ۴) atomize
```
python -m exambank atomize -n smoke -o D:\Projects
```
بررسی `statements_raw.jsonl` و `problems_raw.jsonl` روی ۵ تا ۱۰ گزاره:
1. `truth` را با کلید و polarity خودتان دستی حساب کنید و مقایسه کنید.
2. `text_standalone`: بدون افزودن واقعیت؟ گزاره‌ی غلط «تصحیح» نشده؟
3. گزاره‌هایی با `truth_basis: explanation`: `truth_evidence` واقعاً در پاسخ تشریحی PDF هست؟
4. `atomize_report.json`: تعداد `statements_null_truth` و `statements_needs_review` (برای count_items میانی null طبیعی است).
5. مسئله‌ها: `givens` و `asked` عین متن‌اند؟

## ۵) validate و export
```
python -m exambank validate -n smoke -o D:\Projects
python -m exambank export -n smoke -o D:\Projects --sqlite
```
(یا یک‌جا: `run-all` که اکنون همه‌ی مرحله‌ها تا export را دارد.) بررسی:
1. `validation_report.json`: `rejected` باید ۰ باشد؛ `pydantic.available` روی ماشین شما باید `true` و `disagreements` برابر ۰ باشد (اگر نه، نمونه‌ها را بدهید).
2. `export_report.json`: تعداد گزاره، `statements_truth_known`، `statements_needs_review`.
3. `SCHEMA.md` و `schema\*.schema.json`: قرارداد برای Frankenstein.

## ۶) بازبینی انسانی
`review_queue.csv` را با Excel باز کنید (فارسی درست دیده می‌شود). برای هر ردیف ستون‌های `decision` (approved / edited / rejected)، `truth_new` (true / false / null)، `text_new` و `note` را پر کنید و ذخیره کنید (CSV UTF-8؛ جداکننده‌ی `;` هم پذیرفته می‌شود). سپس:
```
python -m exambank import-review -n smoke -o D:\Projects --review-csv D:\Projects\smoke\review_queue.csv
python -m exambank export -n smoke -o D:\Projects
```
بررسی: گزاره‌ی ویرایش‌شده `truth_basis: manual` و `review_status: edited` دارد؛ رد‌شده در `rejected_by_review.jsonl` است. **سپس کل pipeline را با `--restart` دوباره اجرا کنید؛ ویرایش‌ها باید باقی بمانند.**

## ۷) اجرای دوباره = بدون مصرف سهمیه
```
python -m exambank run-all -n smoke -i exam.pdf -p "41-42, 87" -o D:\Projects --exam-id bio-1403-t --year 1403 --group تجربی
```
همه‌ی مرحله‌ها باید `از قبل انجام‌شده` شوند و هیچ فراخوانی انجام نشود. `--restart` هم باید از کش پاسخ بگیرد.

## ۸) ادغام چند سال (بانک اصلی)
بعد از ساخت پروژه‌های جدا برای هر سال (هر کدام با `--exam-id` و `--year` خودش):
```
python -m exambank merge -n master -o D:\Projects --from D:\Projects\bio1402 D:\Projects\bio1403 --sqlite
```
در `D:\Projects\master\statements.jsonl` به `dedupe.occurrences` و `years` نگاه کنید؛ `cross_year_truth_conflict` را در `review_queue.csv` بررسی کنید.

## ۹) شکل قدیمی CLI
```
python -m exambank -n Bio10.3 -i book.pdf -p "250-255, 260" -o D:\Projects
```
(extract + docx). `python Extraction.WorkFlow.py ...` قدیمی تا M5 دست‌نخورده کار می‌کند.

