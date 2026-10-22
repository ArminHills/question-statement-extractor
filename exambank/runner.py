# -*- coding: utf-8 -*-
"""اجرای ایمن یک مرحله روی فهرستی از کلیدها (صفحه/سوال) با state و توقف هوشمند.
(ت): سهمیه‌ی روزانه یا چند 429 پیاپی ← توقف با ذخیره‌ی وضعیت، نه تکرار برای تک‌تک صفحه‌ها."""
import time
from dataclasses import dataclass, field

from .client import LLMFatalError, LLMError, LLMQuotaExhausted, LLMRateLimited


@dataclass
class RunSummary:
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    not_run: list = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def ok(self):
        return not self.failed and not self.stopped_reason


def run_items(keys, fn, state, stage, log=print, delay=3, stop_event=None,
              sleep=time.sleep, max_consecutive_rate_limits=2):
    s = RunSummary()
    if state.quota:
        log(f"[WARN] آخرین اجرا به‌دلیل سهمیه متوقف شده بود ({state.quota.get('reason')}). تلاش دوباره...")
    consecutive_429 = 0
    ran_any = False
    for i, key in enumerate(keys):
        if state.is_done(stage, key):
            s.skipped.append(key)
            continue
        if stop_event is not None and stop_event.is_set():
            s.stopped_reason = "user_stop"
            s.not_run = [k for k in keys[i:] if not state.is_done(stage, k)]
            log("[INFO] پردازش توسط کاربر متوقف شد.")
            return s
        if ran_any and delay:
            sleep(delay)
        ran_any = True
        try:
            fn(key)
        except LLMQuotaExhausted as e:
            state.set_quota_exhausted(stage, str(e))
            s.stopped_reason = "quota_exhausted"
            s.not_run = [k for k in keys[i:] if not state.is_done(stage, k)]
            log(f"[FATAL] سهمیه‌ی روزانه تمام شد؛ وضعیت ذخیره شد و پردازش متوقف شد. {e}")
            return s
        except LLMFatalError as e:
            s.stopped_reason = "fatal"
            s.not_run = [k for k in keys[i:] if not state.is_done(stage, k)]
            log(f"[FATAL] خطای غیرقابل‌تکرار ({e.code}): {e}. پردازش متوقف شد.")
            return s
        except LLMRateLimited as e:
            consecutive_429 += 1
            state.mark_failed(stage, key, e)
            s.failed.append(key)
            log(f"[ERROR] {key}: 429 پس از همه‌ی تلاش‌ها.")
            if consecutive_429 >= max_consecutive_rate_limits:
                state.set_quota_exhausted(stage, "429 پیاپی؛ احتمالاً سهمیه تمام شده")
                s.stopped_reason = "quota_exhausted"
                s.not_run = [k for k in keys[i + 1:] if not state.is_done(stage, k)]
                log("[FATAL] چند 429 پیاپی؛ وضعیت ذخیره و پردازش متوقف شد.")
                return s
            continue
        except LLMError as e:
            consecutive_429 = 0
            state.mark_failed(stage, key, e)
            s.failed.append(key)
            log(f"[ERROR] {key}: {e}")
            continue
        consecutive_429 = 0
        if state.quota:
            state.clear_quota()
        state.mark_ok(stage, key)
        s.done.append(key)
    return s
