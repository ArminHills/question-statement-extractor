# DESIGN.md — ابزار بانک گزاره و مسئله‌ی کنکور زیست

## وضعیت (پایان M3)
| بخش | وضعیت |
|---|---|
| truth_rules، normalize، parsing، cache، state، client/retry/runner، رفع باگ‌های الف/ب/پ/ت | تست‌شده |
| extract، assemble، CLI، docx اختیاری (M2) | تست‌شده با Mock و PDF ساختگی؛ در محیط شما سبز بود |
| taxonomy (`build-taxonomy`، بارگذاری/اعتبارسنجی، شناسه‌های پایدار) | تست‌شده با Mock و داده‌ی **ساختگی**؛ taxonomy واقعی هنوز ساخته نشده (نیاز به صفحه‌های فهرست شما) |
| classify | تست‌شده با Mock |
| atomize (گزاره‌ها) و problems (مسئله‌ها) و راستی‌آزمایی ادعاهای LLM | تست‌شده با Mock؛ ۴ جهش عمدی در منطق اصلی همگی توسط تست‌ها گرفته شد |
| `GeminiClient` واقعی، کیفیت واقعی prompt‌ها (extract/classify/atomize/problem/toc)، bbox روی PDF واقعی | **اجرا نشده** (کلید/شبکه ندارم) |
| validate/dedupe/export/review_queue/merge/SCHEMA.md/schema/*.schema.json، evaluate، GUI، wrapper نازک | **هنوز نوشته نشده** (M4 و M5) |

`Extraction.WorkFlow.py` هنوز دست‌نخورده است. همه‌ی قابلیت‌ها از `python -m exambank` در دسترس‌اند.

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

14. **classify کلید را نمی‌بیند**: ورودی فقط صورت سوال، گزینه‌ها و شکل‌هاست؛ پس polarity فقط از صورت سوال می‌آید و کلید روی آن اثر نمی‌گذارد. خروجی خام LLM در `work/classify/<qid>.json` ذخیره می‌شود و پرچم‌های قاعده‌محور هنگام جمع‌کردن (`gather_classified`) اعمال می‌شوند؛ تغییر قواعد نیاز به فراخوانی دوباره ندارد.
15. **پرچم‌های classify**: `polarity_evidence_not_found` (نقل در صورت سوال نیست)، `polarity_disagreement` (نشانه‌های واژگانی صورت سوال با polarity نمی‌خواند؛ «به‌جز» نشانه‌ی قوی منفی است و در بدترین حالت یک پرچم اضافه می‌دهد)، `polarity_missing`، `structure_option_mismatch`، `route_structure_mismatch`، `figure_context_mismatch`. `topic_ids` نامعتبر (خارج از taxonomy) = پاسخ نامعتبر = شکست و تلاش ترمیمی، نه پرچم.
16. **LLM هرگز truth نمی‌دهد**. در atomize فقط متن می‌سازد و حکم صریح پاسخ تشریحی را با `evidence` گزارش می‌کند. کد این‌ها را راستی‌آزمایی می‌کند: evidence و why_false باید عیناً (با تطبیق بی‌حساس به نشانه‌گذاری/نیم‌فاصله/ی‌ک/ارقام) در پاسخ تشریحی باشند وگرنه نادیده گرفته می‌شوند و `evidence_not_found` / `why_false_not_found` ثبت می‌شود؛ متن آیتم‌ها باید در صورت سوال باشد (`item_text_not_in_stem`)؛ `text_original` گزینه‌ها از منبع می‌آید، نه از LLM؛ `text_standalone` با نسبت واژه‌ی تازه (>۰٫۵) به `standalone_adds_words` پرچم می‌خورد؛ `context_ref` باید یکی از figure_idهای همان سوال یا `shared_scenario` باشد.
17. **معنای گزینه‌های combo/count**: تحلیل‌گر قاعده‌محور (`parsing.py`) اولویت دارد؛ معنای LLM فقط وقتی استفاده می‌شود که تحلیل‌گر نتواند (و آن‌گاه `option_semantics_llm_only`) یا برای cross-check (`option_parse_disagreement`، تحلیل‌گر برنده است). پرچم فقط برای گزینه‌ی کلید زده می‌شود. تحلیل‌گر عدد را تا «هشت» و برچسب‌های موارد را فقط از همان‌هایی که LLM از صورت سوال گزارش کرده می‌پذیرد؛ چیز دیگری ← null.
18. **تجزیه‌ی گزینه‌ی مرکب** (`parts`) فقط وقتی پذیرفته می‌شود که هر جزء حکم صریح با evidence تأییدشده داشته باشد و متن جزء در متن گزینه باشد؛ وگرنه گزینه مرکب (`is_atomic=false`) و کامل می‌ماند (`parts_rejected`). جزءها با `parent_statement_id` و `statement_id` به شکل `<parent>_p<k>` ثبت می‌شوند.
19. **شناسه‌ها**: `question_id = <exam_id>_q<NNN>`؛ گزاره‌ی گزینه `…_o<برچسب>`؛ گزاره‌ی مورد (الف..د) `…_i<شماره‌ی ترتیبی>` (ASCII پایدار؛ برچسب فارسی در `source.option_label`)؛ مسئله `…_prob`. mixed هم در statements و هم در problems می‌آید.
20. **فیلدهای افزوده به statement** (به قرارداد حداقلی اضافه شدند، چیزی حذف نشد): `truth_evidence`، `distractor_type`، `parent_statement_id`، `polarity`. فیلدهای افزوده به problem: `stem_original`، `answer_status`، `options[].text_original`.
21. **کلاس مسئله** قابل‌گسترش است: نام snake_case جدید پذیرفته و با `new_problem_class` پرچم می‌خورد. نوع جدیدی برای `question_type` یا `structure` اضافه نشد.
22. **محدودیت‌های شناخته‌شده**: `batch_size` غیر از 1 هنوز پیاده‌سازی نشده (هشدار می‌دهد و یک سوال در هر فراخوانی اجرا می‌کند)؛ classify/atomize از `response_schema` استفاده نمی‌کنند و مسیر «JSON + اعتبارسنج + یک ترمیم» را می‌روند؛ ویرایش دستی بازبین (`import-review`) و حفظ آن در اجرای دوباره در M4 است.

## قراردادهای داده
در M2 با Pydantic ثبت می‌شود؛ قرارداد حداقلی prompt شما حفظ و فقط فیلد اضافه می‌شود.
