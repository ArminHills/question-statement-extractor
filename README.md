# ابزار بانک گزاره و مسئله‌ی کنکور زیست

PDF سوالات و پاسخ‌های کنکور (فقط زیست‌شناسی) ← دو بانک ساختاریافته: **گزاره‌ها** (هر گزاره با صحیح/غلط، فقط از پاسخنامه) و **مسئله‌ها**.
خروجی ورودی برنامه‌ی Frankenstein است؛ قرارداد دقیق: `SCHEMA.md` (در هر export ساخته می‌شود).

## نصب
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .      # باید بدون خطا و (با pydantic نصب‌شده) بدون skip باشد
```
کلید: فایل `.env` کنار برنامه: `GEMINI_API_KEY=...` (یا متغیر محیطی). فقط ابزارها و سهمیه‌ی رایگان.

## اجرا
بدون آرگومان = GUI (`python Extraction.WorkFlow.py`). با آرگومان = CLI. CLI قدیمی بدون تغییر کار می‌کند:
```
python Extraction.WorkFlow.py -n Bio10.3 -i book.pdf -p "250-255, 260" -o D:\Projects        # extract + فایل Word
```
خط‌لوله‌ی کامل (هر مرحله دستور مستقل با ورودی/خروجی JSON روی دیسک):
```
python -m exambank build-taxonomy -n tax -i fehrest10.pdf -p "3-4" -o D:\P --book-id bio10 --grade 10 --book-title "زیست‌شناسی دهم"   # یک‌بار برای هر کتاب
python -m exambank run-all -n bio1403 -i exam.pdf -p "1-60" -o D:\P --exam-id bio-1403-t --year 1403 --group تجربی [--manifest m.yaml] [--layout auto|interleaved|separate] [--sqlite] [--docx]
python -m exambank run-all ... --stages extract,assemble     # فقط بخشی از مراحل: extract, assemble, classify, atomize, validate, export
python -m exambank import-review -n bio1403 -o D:\P --review-csv D:\P\bio1403\review_queue.csv   # بازبینی انسانی، بعد: export
python -m exambank merge -n master -o D:\P --from D:\P\bio1402 D:\P\bio1403 --sqlite            # ادغام سال‌ها + dedupe بین سال‌ها
python -m exambank evaluate -n bio1403 -o D:\P --gold gold                                      # ارزیابی با داده‌ی طلایی (gold/README.md)
```
## خروجی‌ها (`<output_dir>/<project>/`)
`questions.jsonl` · `statements.jsonl` · `problems.jsonl` · `statements.csv` · `review_queue.csv` · `figures/` · `schema/*.schema.json` · `SCHEMA.md` · `state.json` · اختیاری `bank.sqlite` و `<project>.docx`.

## اصل‌ها
- صحیح/غلط هر گزاره را **کد** از کلید یا حکم صریح پاسخ تشریحی (با نقل عین) می‌گیرد؛ نه LLM و نه دانش زیستی. نامعلوم = `null` + پرچم بازبینی.
- هر ادعای LLM (نقل‌ها، متن آیتم‌ها، معنای گزینه‌ها) قبل از استفاده راستی‌آزمایی می‌شود.
- هر فراخوانی LLM روی دیسک کش می‌شود؛ اجرای دوباره (و `--restart`) سهمیه مصرف نمی‌کند و برای پاسخ کش‌شده صبر نمی‌کند. اتمام سهمیه‌ی روزانه تشخیص داده می‌شود و کار با ذخیره‌ی وضعیت متوقف می‌شود؛ روز بعد از همان‌جا ادامه می‌یابد.
- ویرایش‌های دستی بازبین جدا نگه‌داری می‌شوند و اجرای دوباره‌ی مراحل آن‌ها را بازنویسی نمی‌کند.

اسناد: `DESIGN.md` (معماری، تصمیم‌ها، محدودیت‌ها) · `SCHEMA.md` · `smoke_test.md` (اجرای واقعی روی ۳ صفحه) · `gold/README.md`.
