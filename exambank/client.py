# -*- coding: utf-8 -*-
"""لایه‌ی LLMClient: Gemini واقعی، Mock برای تست، و کش.

رفع باگ‌ها:
  (الف) پاسخ خالی/قطع‌شده/مسدود = شکست (LLMInvalidResponse)، نه موفقیت.
  (ت) 429 مربوط به سهمیه‌ی روزانه فوراً LLMQuotaExhausted می‌شود (بدون retry).
"""
import json
import re
import time

from .cache import make_key
from .constants import PROMPT_VERSION, SCHEMA_VERSION

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 5
FATAL_STOP_CODES = {401, 403, 404}


class LLMError(Exception):
    pass


class LLMFatalError(LLMError):
    """401/403/404: تکرار بی‌فایده است و همه‌ی صفحه‌ها همین خطا را می‌گیرند."""
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class LLMQuotaExhausted(LLMError):
    """سهمیه‌ی روزانه تمام شده؛ ادامه‌دادن فقط وقت تلف می‌کند."""


class LLMRateLimited(LLMError):
    """429 (غیرروزانه) که پس از همه‌ی تلاش‌ها هنوز برطرف نشده."""


class LLMTransientError(LLMError):
    pass


class LLMInvalidResponse(LLMError):
    """پاسخ خالی، قطع‌شده (finish_reason غیر STOP)، مسدود یا JSON/schema نامعتبر."""


_DAILY_RE = re.compile(r"per\s*day|perday|daily|requests_per_day", re.I)


def is_daily_quota_error(text):
    return bool(_DAILY_RE.search(text or ""))


def check_response(text, finish_reason=None, block_reason=None, has_candidates=True):
    """فقط پاسخِ کامل (STOP) و غیرخالی پذیرفته می‌شود."""
    if block_reason:
        raise LLMInvalidResponse(f"پاسخ مسدود شد: {block_reason}")
    if not has_candidates:
        raise LLMInvalidResponse("پاسخی (candidate) برنگشت")
    name = str(getattr(finish_reason, "name", finish_reason) or "").split(".")[-1].upper()
    if name != "STOP":
        raise LLMInvalidResponse(f"پاسخ ناقص یا غیرعادی: finish_reason={name or 'نامشخص'}")
    if not (text or "").strip():
        raise LLMInvalidResponse("پاسخ خالی")
    return text


def parse_json_text(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError as e:
        raise LLMInvalidResponse(f"JSON نامعتبر: {e}")


def map_exception(e):
    """نگاشت خطای SDK (با duck typing روی .code و .message) به خطاهای خودمان."""
    code = getattr(e, "code", None)
    msg = f"{getattr(e, 'message', '') or ''} {getattr(e, 'details', '') or ''} {e}"
    if code in FATAL_STOP_CODES:
        return LLMFatalError(f"{code}: {getattr(e, 'message', e)}", code)
    if code == 429:
        if is_daily_quota_error(msg):
            return LLMQuotaExhausted(f"سهمیه‌ی روزانه تمام شده: {getattr(e, 'message', e)}")
        return LLMRateLimited(str(getattr(e, "message", e)))
    if isinstance(code, int) and code >= 500:
        return LLMTransientError(f"{code}: {getattr(e, 'message', e)}")
    if isinstance(e, LLMError):
        return e
    return LLMTransientError(str(e))


def call_with_retry(fn, log=print, sleep=time.sleep, max_retries=MAX_RETRIES, base=BASE_BACKOFF_SECONDS):
    """backoff نمایی برای 429 و خطای سرور؛ توقف فوری برای fatal و سهمیه‌ی روزانه و پاسخ نامعتبر."""
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as raw:  # noqa: BLE001
            err = map_exception(raw)
            if isinstance(err, (LLMFatalError, LLMQuotaExhausted, LLMInvalidResponse)):
                raise err
            last = err
            wait = base * (2 ** (attempt - 1))
            kind = "محدودیت نرخ (429)" if isinstance(err, LLMRateLimited) else "خطای موقت"
            log(f"[RETRY] {kind} — تلاش {attempt}/{max_retries}. {wait} ثانیه صبر...")
            if attempt < max_retries:
                sleep(wait)
    raise last


class LLMClient:
    def generate_json(self, *, task, system_prompt, parts, schema=None, validator=None, model=None):
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """responses: تابع (task, parts) -> داده، یا دیکشنری task -> داده/تابع. همه‌ی فراخوانی‌ها ثبت می‌شوند."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def generate_json(self, *, task, system_prompt, parts, schema=None, validator=None, model=None):
        self.calls.append({"task": task, "parts": parts})
        r = self.responses(task, parts) if callable(self.responses) else self.responses[task]
        if callable(r):
            r = r(parts)
        if isinstance(r, Exception):
            raise r
        if validator is not None:
            try:
                validator(r)
            except Exception as e:  # noqa: BLE001
                raise LLMInvalidResponse(f"schema نامعتبر: {e}")
        return r


class CachedClient(LLMClient):
    def __init__(self, inner, cache, model, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION):
        self.inner, self.cache, self.model = inner, cache, model
        self.prompt_version, self.schema_version = prompt_version, schema_version

    def generate_json(self, *, task, system_prompt, parts, schema=None, validator=None, model=None):
        m = model or self.model
        key = make_key(task, system_prompt, parts, self.prompt_version, m, self.schema_version)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        result = self.inner.generate_json(task=task, system_prompt=system_prompt, parts=parts,
                                          schema=schema, validator=validator, model=m)
        self.cache.put(key, result)
        return result


class GeminiClient(LLMClient):
    """پیاده‌سازی واقعی. در این محیط اجرا/تست نشده (google-genai نصب نیست)؛
    منطق تشخیص خطا و پاسخ نامعتبر در توابع بالا تست شده است."""
    def __init__(self, api_key, model, temperature=0, log=print, sleep=time.sleep):
        from google import genai  # import تنبل: تست‌ها به SDK نیاز ندارند
        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model, self.temperature, self.log, self.sleep = model, temperature, log, sleep

    def _once(self, system_prompt, parts, schema, model):
        from google.genai import types
        contents = []
        for kind, content in parts:
            if kind == "text":
                contents.append(content)
            else:
                contents.append(types.Part.from_bytes(data=content, mime_type="application/pdf"))
        cfg = dict(system_instruction=system_prompt, temperature=self.temperature,
                   response_mime_type="application/json")
        if schema is not None:
            cfg["response_schema"] = schema
        resp = self.client.models.generate_content(
            model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
        cands = getattr(resp, "candidates", None) or []
        fr = cands[0].finish_reason if cands else None
        pf = getattr(resp, "prompt_feedback", None)
        block = getattr(pf, "block_reason", None) if pf else None
        text = check_response(resp.text, fr, block, bool(cands))  # باگ (الف): خالی/قطع‌شده = شکست
        return parse_json_text(text)

    def generate_json(self, *, task, system_prompt, parts, schema=None, validator=None, model=None):
        model = model or self.model
        use_schema = schema
        try:
            data = call_with_retry(lambda: self._once(system_prompt, parts, use_schema, model),
                                   self.log, self.sleep)
        except LLMError:
            raise
        except Exception as e:  # SDK ممکن است بعضی ویژگی‌های schema را نپذیرد ← حالت JSON ساده
            if schema is None:
                raise
            self.log(f"[WARN] schema پذیرفته نشد ({e}); حالت JSON + اعتبارسنجی.")
            data = call_with_retry(lambda: self._once(system_prompt, parts, None, model), self.log, self.sleep)
        if validator is None:
            return data
        try:
            validator(data)
            return data
        except Exception as first:  # noqa: BLE001  یک تلاش ترمیمی
            repair = [("text", "خروجی قبلی شما با schema سازگار نبود:\n" + str(first)
                       + "\nخروجی قبلی:\n" + json.dumps(data, ensure_ascii=False)
                       + "\nفقط JSON اصلاح‌شده را برگردان.")]
            data2 = call_with_retry(lambda: self._once(system_prompt, list(parts) + repair, None, model),
                                    self.log, self.sleep)
            try:
                validator(data2)
            except Exception as second:  # noqa: BLE001
                raise LLMInvalidResponse(f"schema حتی پس از ترمیم نامعتبر است: {second}")
            return data2
