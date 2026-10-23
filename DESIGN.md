# DESIGN.md — ابزار بانک گزاره و مسئله‌ی کنکور زیست

## وضعیت (پایان M2)
| بخش | وضعیت |
|---|---|
| truth_rules، normalize، cache، state، client/retry/runner، رفع باگ‌های الف/ب/پ/ت | تست‌شده (M1) |
| assemble (stitching، اتصال، manifest، گزارش مشکل) | تست‌شده |
| extract (prompt نسخه‌دار، اعتبارسنج صفحه، رندر/برش شکل با pypdfium2، state، docx اختیاری) | تست‌شده با Mock و PDF ساختگی واقعی |
| CLI `python -m exambank` (زیردستورها + شکل قدیمی `-n -m -i -p -o --restart`) | تست‌شده با Mock |
| `GeminiClient` واقعی، کیفیت واقعی prompt، مختصات bbox روی PDF واقعی | **اجرا نشده** (نیاز به کلید/شبکه؛ مرحله‌ی چشمی در smoke_test.md) |
| مدل‌های Pydantic (`build_pydantic_models`) | نوشته شد؛ تست‌شان `skip` می‌شود مگر pydantic نصب باشد (در محیط نویسنده نبود) |
| classify، atomize، problems، validate/dedupe/export/review، taxonomy، evaluate، GUI، wrapper نازک | **هنوز نوشته نشده** (M3 تا M5) |

`Extraction.WorkFlow.py` هنوز دست‌نخورده است (CLI/GUI قدیمی مثل قبل کار می‌کنند). همان CLI قدیمی با `python -m exambank -n ... -i ... -p ... -o ...` هم اجرا می‌شود (extract + docx). تبدیل فایل قدیمی به wrapper نازک همراه GUI در M5 انجام می‌شود.

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

10. **اعتبارسنج صفحه** (`schemas.validate_page_payload`، پایتون خالص) مرجع واقعیِ پذیرش خروجی extract است؛ مدل Pydantic فقط برای `response_schema` و تولید JSON Schema است. دلیل: تا وقتی pydantic را اجرا نکرده‌ام نمی‌خواهم پذیرش داده به کدی وابسته باشد که تست‌نشده است. لیست‌های `questions/answers/figures` باید صریحاً وجود داشته باشند؛ غیبت‌شان یا عبارت جایگزین مثل «هیچ سوالی یافت نشد» شکست است (باگ ب).
11. **شکل‌ها**: `box_2d=[ymin,xmin,ymax,xmax]` مقیاس ۰..۱۰۰۰ (مستندات رسمی Gemini برای *تصویر*). برای PDF تأیید نشده؛ پس bbox نامعتبر یا کوچک‌تر از ۱٪ صفحه ← ذخیره‌ی کل صفحه با `bbox_status=full_page_fallback`. figure_id را کد می‌سازد: `<pdf>-p<صفحه>-f<n>`. توضیح فارسی شکل `is_ai_description=true` است و هرگز مبنای truth نیست.
12. **کش مشترک** در `<output_dir>/.llm_cache` است، نه داخل پروژه. `--restart` فقط state را پاک می‌کند؛ اجرای دوباره با ورودی/prompt/مدل یکسان سهمیه مصرف نمی‌کند.
13. **manifest** نمونه: `manifest.example.yaml`.

## قراردادهای داده
در M2 با Pydantic ثبت می‌شود؛ قرارداد حداقلی prompt شما حفظ و فقط فیلد اضافه می‌شود.
