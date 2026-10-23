# smoke_test.md — اجرای واقعی روی ۳ صفحه (تا پایان M2: extract + assemble)

## ۰) آماده‌سازی
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .        # باید سبز باشد (pydantic نصب باشد، ۳ تست skip نمی‌شود)
```
کلید را در فایل `.env` کنار برنامه بگذارید: `GEMINI_API_KEY=...` (یا در محیط).

## ۱) سه صفحه‌ی نماینده را انتخاب کنید
بهتر است شامل این‌ها باشد: (الف) صفحه‌ای با سوال بریده‌شده بین دو صفحه، (ب) صفحه‌ای با شکل/نمودار، (پ) صفحه‌ی کلید پاسخ.
مثال: صفحه‌های `41, 42` سوال و `87` کلید. اگر پاسخ در بخش جدا است، `manifest.example.yaml` را کپی و ویرایش کنید.

## ۲) extract
```
python -m exambank extract -n smoke -i exam.pdf -p "41-42, 87" -o D:\Projects
```
انتظار: سه خط `[SUCCESS]`، سه فایل در `D:\Projects\smoke\pages\`. اگر خطای 429 روزانه گرفتید باید فوراً با پیام «سهمیه‌ی روزانه تمام شد» متوقف شود و `state.json` مقدار `quota` داشته باشد.

## ۳) بررسی چشمی (مهم‌ترین قدم)
1. `pages\*.json`: متن سوال‌ها را با PDF مقایسه کنید (عینیت، شماره، برچسب گزینه‌ها).
2. صفحه‌ی بریده‌شده: `continues_on_next_page` و `continues_from_previous_page` درست باشد.
3. صفحه‌ی کلید: هر خانه یک (number, correct) باشد؛ ستون‌ها جابه‌جا نشده باشند.
4. **bbox شکل‌ها**: پوشه‌ی `figures\` را باز کنید. اگر برش‌ها درست‌اند (`bbox_status: cropped`) عالی است. اگر برش‌ها جابه‌جا/کوچک‌اند یا همیشه `full_page_fallback` می‌گیرید، یعنی چارچوب مختصات Gemini روی PDF با فرض ما (کل صفحه) نمی‌خواند؛ نمونه‌ی `pages\*.json` و `figures\*` را به من بدهید تا اصلاح کنم.

## ۴) assemble
```
python -m exambank assemble -n smoke -i exam.pdf -o D:\Projects --exam-id bio-1403-t --year 1403 --group تجربی --layout auto
```
(یا `--manifest manifest.yaml`). خروجی: `assembled.jsonl` و `assemble_report.json`. با سه صفحه‌ی نمونه ممکن است `missing_question_numbers` و `question_without_answer` بیاید؛ طبیعی است چون کل آزمون را نداده‌اید.

## ۵) اجرای دوباره = بدون مصرف سهمیه
```
python -m exambank run-all -n smoke -i exam.pdf -p "41-42, 87" -o D:\Projects --exam-id bio-1403-t
```
باید همه‌ی صفحه‌ها `از قبل انجام‌شده` شوند و هیچ فراخوانی انجام نشود. با `--restart` هم باید از کش پاسخ بگیرد.

## ۶) شکل قدیمی CLI
```
python -m exambank -n Bio10.3 -i book.pdf -p "250-255, 260" -o D:\Projects
```
extract + فایل `Bio10.3.docx` (و `python Extraction.WorkFlow.py ...` قدیمی هم تا M5 دست‌نخورده کار می‌کند).
