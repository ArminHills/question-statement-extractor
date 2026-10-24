# gold/ — داده‌ی طلایی شما

فایل‌های `*.jsonl` این پوشه (هر خط یک رکورد، UTF-8) را `python -m exambank evaluate` می‌خواند و با خروجی پروژه مقایسه می‌کند.
فقط فیلدهایی که می‌گذارید سنجیده می‌شوند؛ پس می‌توانید با ۲۰–۳۰ سوال دستی شروع کنید. شناسه‌ها باید همان شناسه‌های خط‌لوله باشند.

**رکورد سوال** (`question_id` = `<exam_id>_q<NNN>`):
```json
{"question_id": "bio-1403-t_q012", "correct": ["3"], "answer_status": "official", "structure": "single_best_statement", "polarity": "negative", "route": "statement", "question_type": "textbook_recall", "topic_ids": ["bio10-ch02-s01"]}
```
**رکورد گزاره** (`statement_id` = `<question_id>_o<برچسب>` یا `<question_id>_i<شماره‌ی مورد>`):
```json
{"statement_id": "bio-1403-t_q012_o3", "truth": false}
```
- `truth: null` یعنی «پاسخنامه این را مشخص نمی‌کند»؛ خط‌لوله هم باید null بدهد (اگر truth ادعا کند، `false_positive_on_gold_null` شمرده می‌شود).
- `polarity: null` معتبر است (مثلاً برای سوال محاسبه‌ای).
- فایل‌های `*.example` این پوشه فقط نمونه‌اند و خوانده نمی‌شوند. حقیقتِ gold را از خود PDF و پاسخنامه بنویسید، نه از خروجی ابزار.

اجرا: `python -m exambank evaluate -n <پروژه> -o <پوشه> --gold gold` (بعد از `export`). گزارش: `evaluation_report.json`.
**مهم:** پیش‌بینی‌هایی که بازبین انسانی عوض کرده (`truth_basis=manual`) از دقت حذف می‌شوند، چون دیگر ماشینی نیستند.
