# DESIGN.md — ابزار بانک گزاره و مسئله‌ی کنکور زیست

## وضعیت (M1 و بخش‌هایی از M2/M3)
| بخش | وضعیت |
|---|---|
| قواعد truth (`truth_rules.py`) | پیاده‌سازی و تست‌شده (35 تست) |
| نرمال‌سازی فارسی/نمادهای زیستی (`normalize.py`) | پیاده‌سازی و تست‌شده (32 تست) |
| کش، state، client، retry، runner، رفع باگ‌های ۱۰ الف/پ/ت (`cache/state/client/runner`) | تست‌شده با Mock (23 تست) |
| assemble: stitching، اتصال سوال-پاسخ، manifest، گزارش مشکل (`assemble.py`) | تست‌شده (22 تست) |
| `GeminiClient` واقعی | نوشته شد، **اجرا نشده** (google-genai در محیط من نبود) |
| extract (prompt، schema، رندر شکل)، classify، atomize، problems، validate/dedupe/export/review، taxonomy، evaluate، GUI، wrapper نازک | **هنوز نوشته نشده** (M2 به بعد) |
| Pydantic schemas | **هنوز نوشته نشده**؛ pydantic در محیط من نبود و نمی‌خواهم کد اجرا‌نشده تحویل دهم |

`Extraction.WorkFlow.py` فعلاً دست‌نخورده است، پس CLI/GUI قدیمی مثل قبل کار می‌کند. تبدیل آن به wrapper نازک همراه extract انجام می‌شود.

## معماری
`exambank/` ماژول‌های: `constants` `utils` `normalize` `truth_rules` `cache` `state` `client` `runner` `assemble` (و بعداً `schemas` `stages/*` `cli` `gui`).
هر مرحله ورودی/خروجی JSON روی دیسک دارد. ماژول‌های منطقی عمداً به pydantic/SDK وابسته نیستند تا بدون API تست شوند.

## تصمیم‌ها و فرض‌ها
1. **کش**: کلید = sha256(task، هش متن prompt، هش ورودی، PROMPT_VERSION، مدل، SCHEMA_VERSION). هش *متن* prompt هم وارد شده تا ویرایش بدون بالابردن نسخه، پاسخ کهنه ندهد. شکست هرگز کش نمی‌شود؛ لیست خالی (صفحه‌ی بدون سوال) معتبر و کش‌شدنی است.
2. **پاسخ موفق** فقط وقتی `finish_reason==STOP` و متن غیرخالی و JSON معتبر باشد. غیر از این `LLMInvalidResponse` (باگ الف).
3. **سهمیه**: 429 که پیامش «PerDay/daily» دارد فوراً `LLMQuotaExhausted`؛ دو 429 پیاپی (قابل تنظیم) هم همین‌طور. runner state را ذخیره و متوقف می‌شود (باگ ت). تشخیص بر اساس متن خطا است؛ اگر Gemini قالب پیام را عوض کرد، فقط `_DAILY_RE` در `client.py` باید به‌روز شود.
4. **resume** فقط از `state.json` (باگ پ). صفحه‌ی failed در اجرای بعد دوباره تلاش می‌شود.
5. **stitching**: فقط بین صفحه‌های متوالیِ یک PDF؛ الحاق با یک فاصله؛ گزینه‌ی بریده با `is_continuation` (در prompt extract باید خواسته شود). ناسازگاری ← issue، نه حدس.
6. **manifest**: هر exam شامل `question_pages`، `answer_pages`، و اختیاری `question_pdf`/`answer_pdf` (برای دو PDF جدا). صفحه‌ی خارج از manifest رها و گزارش می‌شود. `--layout` فقط هشدار `unexpected_answer_location` می‌دهد؛ اتصال همیشه با (exam_id, number) است.
7. **truth**: اگر حکم صریح پاسخ تشریحی با کلید ناسازگار بود، مقدار *حکم صریح* نگه داشته و `conflict_explanation_vs_key` ثبت می‌شود (تصمیم با بازبین). `annulled/disputed` همیشه null. هر truth برابر null همیشه دلیل بازبینی دارد. در count_items حالت میانی اگر احکام صریح اجازه‌ی استنتاج قطعی بدهند (مثلاً c=2 و دو مورد صریحاً صحیح) باقی‌ماندها `key_derived` می‌شوند.
8. **نمادها**: رقم و +/− ← یونیکد (Na⁺ Ca²⁺ CO₂)؛ هر بالا/پایین‌نویس با حرف ← همیشه `X^{A}` / `_{n}`؛ فرمول/یون فقط از فهرست سفید. نیم‌فاصله‌ی «می/نمی»، «ها/های/تر/ترین»، «ه ی» اصلاح می‌شود؛ خطر شناخته‌شده: واژه‌ی مستقل «تر» (به معنی خیس) بعد از حرف فارسی چسبانده می‌شود.
9. برچسب گزینه‌ها بدون تبدیل الف/ب/ج/د ↔ 1..4 نرمال می‌شوند (ابهام ج/پ و د/ت)؛ ناهماهنگی با کلید به‌صورت `key_not_in_options` گزارش می‌شود.

## قراردادهای داده
در M2 با Pydantic ثبت می‌شود؛ قرارداد حداقلی prompt شما حفظ و فقط فیلد اضافه می‌شود.
