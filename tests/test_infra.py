# -*- coding: utf-8 -*-
import os
import tempfile
import threading
import unittest

from exambank.cache import DiskCache, make_key
from exambank.client import (CachedClient, LLMFatalError, LLMInvalidResponse, LLMQuotaExhausted,
                             LLMRateLimited, LLMTransientError, MockLLMClient, call_with_retry,
                             check_response, is_daily_quota_error, map_exception, parse_json_text)
from exambank.runner import run_items
from exambank.state import StateStore


class FakeErr(Exception):
    def __init__(self, code, message="", details=""):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class Enum:
    def __init__(self, name):
        self.name = name


class ResponseChecks(unittest.TestCase):  # باگ (الف)
    def test_ok(self):
        self.assertEqual(check_response('{"a":1}', Enum("STOP")), '{"a":1}')
        self.assertEqual(check_response('x', "FinishReason.STOP"), "x")

    def test_empty_is_failure(self):
        for txt in ("", "   ", None):
            with self.assertRaises(LLMInvalidResponse):
                check_response(txt, Enum("STOP"))

    def test_truncated_is_failure(self):
        with self.assertRaises(LLMInvalidResponse):
            check_response('{"a":', Enum("MAX_TOKENS"))

    def test_blocked_is_failure(self):
        with self.assertRaises(LLMInvalidResponse):
            check_response("x", Enum("STOP"), block_reason="SAFETY")
        with self.assertRaises(LLMInvalidResponse):
            check_response("x", None, None, has_candidates=False)

    def test_missing_finish_reason_is_failure(self):
        with self.assertRaises(LLMInvalidResponse):
            check_response("x", None)

    def test_json_parse(self):
        self.assertEqual(parse_json_text('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_text("[]"), [])  # صفحه‌ی بدون سوال = لیست خالی معتبر
        with self.assertRaises(LLMInvalidResponse):
            parse_json_text("{bad")


class ErrorMapping(unittest.TestCase):
    def test_fatal(self):
        for c in (401, 403, 404):
            self.assertIsInstance(map_exception(FakeErr(c, "x")), LLMFatalError)

    def test_daily_vs_minute_429(self):
        e = FakeErr(429, "quota", "GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        self.assertIsInstance(map_exception(e), LLMQuotaExhausted)
        self.assertIsInstance(map_exception(FakeErr(429, "RequestsPerMinute")), LLMRateLimited)
        self.assertTrue(is_daily_quota_error("requests per day exceeded"))

    def test_server(self):
        self.assertIsInstance(map_exception(FakeErr(503, "x")), LLMTransientError)


class Retry(unittest.TestCase):
    def test_retries_then_succeeds_with_backoff(self):
        waits, n = [], {"i": 0}

        def fn():
            n["i"] += 1
            if n["i"] < 3:
                raise FakeErr(503, "x")
            return "ok"
        self.assertEqual(call_with_retry(fn, log=lambda m: None, sleep=waits.append), "ok")
        self.assertEqual(waits, [5, 10])

    def test_daily_quota_no_retry(self):
        waits, n = [], {"i": 0}

        def fn():
            n["i"] += 1
            raise FakeErr(429, "x", "RequestsPerDay")
        with self.assertRaises(LLMQuotaExhausted):
            call_with_retry(fn, log=lambda m: None, sleep=waits.append)
        self.assertEqual(n["i"], 1)
        self.assertEqual(waits, [])

    def test_fatal_no_retry(self):
        n = {"i": 0}

        def fn():
            n["i"] += 1
            raise FakeErr(403, "denied")
        with self.assertRaises(LLMFatalError):
            call_with_retry(fn, log=lambda m: None, sleep=lambda s: None)
        self.assertEqual(n["i"], 1)

    def test_exhausted_429_raises_rate_limited(self):
        def fn():
            raise FakeErr(429, "RPM")
        with self.assertRaises(LLMRateLimited):
            call_with_retry(fn, log=lambda m: None, sleep=lambda s: None)


class CacheTests(unittest.TestCase):
    def test_second_call_hits_cache_and_key_sensitivity(self):
        with tempfile.TemporaryDirectory() as d:
            mock = MockLLMClient({"t": {"x": 1}})
            c = CachedClient(mock, DiskCache(d), "m1")
            parts = [("text", "سلام")]
            self.assertEqual(c.generate_json(task="t", system_prompt="p", parts=parts), {"x": 1})
            self.assertEqual(c.generate_json(task="t", system_prompt="p", parts=parts), {"x": 1})
            self.assertEqual(len(mock.calls), 1)
            # مدل یا ورودی دیگر ← کلید دیگر
            CachedClient(mock, DiskCache(d), "m2").generate_json(task="t", system_prompt="p", parts=parts)
            c.generate_json(task="t", system_prompt="p", parts=[("pdf", b"\x00")])
            self.assertEqual(len(mock.calls), 3)

    def test_failures_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            seq = [LLMInvalidResponse("خالی"), {"ok": True}]
            mock = MockLLMClient(lambda t, p: seq.pop(0))
            c = CachedClient(mock, DiskCache(d), "m")
            with self.assertRaises(LLMInvalidResponse):
                c.generate_json(task="t", system_prompt="", parts=[("text", "a")])
            self.assertEqual(c.generate_json(task="t", system_prompt="", parts=[("text", "a")]), {"ok": True})

    def test_empty_list_result_is_cached(self):
        with tempfile.TemporaryDirectory() as d:
            mock = MockLLMClient({"t": []})
            c = CachedClient(mock, DiskCache(d), "m")
            c.generate_json(task="t", system_prompt="", parts=[("text", "a")])
            c.generate_json(task="t", system_prompt="", parts=[("text", "a")])
            self.assertEqual(len(mock.calls), 1)

    def test_corrupt_cache_is_miss(self):
        with tempfile.TemporaryDirectory() as d:
            cache = DiskCache(d)
            k = make_key("t", "", [("text", "a")], "v", "m", "1")
            cache.put(k, {"a": 1})
            with open(cache._path(k), "w", encoding="utf-8") as f:
                f.write("{broken")
            self.assertIsNone(cache.get(k))


class Resume(unittest.TestCase):  # باگ‌های (پ) و (ت)
    def test_resume_skips_done_and_retries_failed(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            calls = []

            def fn(k):
                calls.append(k)
                if k == 2 and calls.count(2) == 1:
                    raise LLMInvalidResponse("خالی")
            s1 = run_items([1, 2, 3], fn, st, "extract", log=lambda m: None, delay=0)
            self.assertEqual((s1.done, s1.failed), ([1, 3], [2]))
            st2 = StateStore(os.path.join(d, "state.json"))  # فایل از دیسک خوانده می‌شود
            s2 = run_items([1, 2, 3], fn, st2, "extract", log=lambda m: None, delay=0)
            self.assertEqual((s2.skipped, s2.done, s2.failed), ([1, 3], [2], []))
            self.assertEqual(calls, [1, 2, 3, 2])

    def test_daily_quota_stops_immediately_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            calls = []

            def fn(k):
                calls.append(k)
                if k == 3:
                    raise LLMQuotaExhausted("per day")
            s = run_items([1, 2, 3, 4, 5, 6], fn, st, "extract", log=lambda m: None, delay=0)
            self.assertEqual(calls, [1, 2, 3])
            self.assertEqual(s.stopped_reason, "quota_exhausted")
            self.assertEqual(s.not_run, [3, 4, 5, 6])
            self.assertIsNotNone(StateStore(os.path.join(d, "state.json")).quota)
            # روز بعد: ادامه از صفحه ۳ و پاک‌شدن پرچم
            s2 = run_items([1, 2, 3, 4, 5, 6], lambda k: None, StateStore(os.path.join(d, "state.json")),
                           "extract", log=lambda m: None, delay=0)
            self.assertEqual(s2.done, [3, 4, 5, 6])
            self.assertIsNone(StateStore(os.path.join(d, "state.json")).quota)

    def test_consecutive_429_treated_as_quota(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            calls = []

            def fn(k):
                calls.append(k)
                raise LLMRateLimited("429")
            s = run_items(list(range(10)), fn, st, "extract", log=lambda m: None, delay=0)
            self.assertEqual(len(calls), 2)  # نه ۱۰ بار
            self.assertEqual(s.stopped_reason, "quota_exhausted")

    def test_fatal_stops(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            calls = []

            def fn(k):
                calls.append(k)
                raise LLMFatalError("bad key", 403)
            s = run_items([1, 2, 3], fn, st, "x", log=lambda m: None, delay=0)
            self.assertEqual(calls, [1])
            self.assertEqual(s.stopped_reason, "fatal")

    def test_user_stop(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            ev = threading.Event()

            def fn(k):
                if k == 2:
                    ev.set()
            s = run_items([1, 2, 3], fn, st, "x", log=lambda m: None, delay=0, stop_event=ev)
            self.assertEqual(s.done, [1, 2])
            self.assertEqual(s.not_run, [3])

    def test_delay_skipped_after_cached_items(self):
        """فاصله‌ی بین درخواست‌ها فقط بعد از آیتمی که واقعاً درخواست شبکه فرستاده؛ پاسخ کش‌شده صبر نمی‌خواهد."""
        with tempfile.TemporaryDirectory() as d:
            net = {"n": 0}
            made = {1: True, 2: False, 3: False, 4: True, 5: True}

            def fn(k):
                if made[k]:
                    net["n"] += 1
            waits = []
            run_items([1, 2, 3, 4, 5], fn, StateStore(os.path.join(d, "s.json")), "x", log=lambda m: None, delay=3,
                      sleep=waits.append, request_counter=lambda: net["n"])
            # صبر قبل از ۲ (بعد از ۱: درخواست شد) ✓؛ قبل از ۳ (بعد از ۲: کش) ✗؛ قبل از ۴ (بعد از ۳: کش) ✗؛ قبل از ۵ ✓
            self.assertEqual(waits, [3, 3])

    def test_delay_applied_after_failed_request(self):
        with tempfile.TemporaryDirectory() as d:
            net = {"n": 0}

            def fn(k):
                net["n"] += 1
                if k == 1:
                    raise LLMInvalidResponse("خالی")
            waits = []
            run_items([1, 2], fn, StateStore(os.path.join(d, "s.json")), "x", log=lambda m: None, delay=3,
                      sleep=waits.append, request_counter=lambda: net["n"])
            self.assertEqual(waits, [3])

    def test_request_counter_for_cached_client(self):
        from exambank.client import request_counter_for
        with tempfile.TemporaryDirectory() as d:
            mock = MockLLMClient({"t": {"x": 1}})
            c = CachedClient(mock, DiskCache(d), "m")
            counter = request_counter_for(c)
            c.generate_json(task="t", system_prompt="", parts=[("text", "a")])
            self.assertEqual(counter(), 1)
            c.generate_json(task="t", system_prompt="", parts=[("text", "a")])   # کش
            self.assertEqual(counter(), 1)
            self.assertIsNone(request_counter_for(mock))                          # بدون کش: همیشه فاصله

    def test_delay_between_requests_only(self):
        with tempfile.TemporaryDirectory() as d:
            st = StateStore(os.path.join(d, "state.json"))
            waits = []
            run_items([1, 2, 3], lambda k: None, st, "x", log=lambda m: None, delay=3, sleep=waits.append)
            self.assertEqual(waits, [3, 3])


if __name__ == "__main__":
    unittest.main()
