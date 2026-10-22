# -*- coding: utf-8 -*-
"""ثابت‌های نسخه. هر تغییر در prompt یا schema باید این‌جا نسخه را عوض کند."""
SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "2026-10-22.1"

POLARITIES = ("positive", "negative")
ANSWER_STATUSES = ("official", "annulled", "multiple_keys", "disputed", "missing")
TRUTH_BASES = ("key_derived", "explanation", "unresolved")
