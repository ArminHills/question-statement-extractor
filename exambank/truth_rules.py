# -*- coding: utf-8 -*-
"""قواعد تعیین صحیح/غلط گزاره‌ها — فقط کد پایتون، بدون LLM و بدون دانش زیستی.

ورودی‌ها:
  structure     : single_best_statement | completion | count_items | combo_items |
                  matching | ordering | (هر چیز دیگر ← unresolved)
  polarity      : positive | negative   (فقط از صورت سوال؛ نفیِ داخل گزاره تأثیری ندارد)
  answer_status : official | annulled | multiple_keys | disputed | missing
  correct       : لیست برچسب گزینه‌های کلید
  targets       : برچسب‌های هدف به ترتیب. برای single/completion = برچسب گزینه‌ها؛
                  برای combo/count/matching/ordering = برچسب «موارد» (الف..د یا جفت‌ها)
  combo         : {برچسب گزینه: ComboOption} برای combo/matching/ordering
  counts        : {برچسب گزینه: عدد} برای count_items
  explicit      : {هدف: (bool, نقل عین عبارت)} حکم صریح پاسخ تشریحی

خروجی: لیست TruthResult به ترتیب targets.

اولویت منابع truth:
  (۱) حکم صریح پاسخ تشریحی  → basis = explanation
  (۲) استنتاج قطعی از کلید  → basis = key_derived
  (۳) null                   → basis = unresolved  (و همیشه دلیل بازبینی دارد)
اگر (۱) و (۲) ناسازگار باشند، مقدار (۱) نگه داشته می‌شود و دلیل
conflict_explanation_vs_key ثبت می‌شود (تصمیم نهایی با بازبین انسانی است).
برای answer_status برابر annulled یا disputed همیشه truth=null (حتی با حکم صریح).
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

SINGLE_LIKE = {"single_best_statement", "completion"}
COMBO_LIKE = {"combo_items", "matching", "ordering"}
COUNT_LIKE = {"count_items"}

CONFLICT = "conflict_explanation_vs_key"


@dataclass(frozen=True)
class ComboOption:
    """گزینه‌ی کلید در سوال‌های «موارد الف..د».
    kind: 'set' (مجموعه‌ی مشخص؛ «فقط» هم همین است)، 'all' (همه)، 'none' (هیچ‌کدام)."""
    kind: str
    items: Tuple[str, ...] = ()


@dataclass
class TruthResult:
    target: str
    truth: Optional[bool]
    basis: str  # key_derived | explanation | unresolved
    review_reasons: List[str] = field(default_factory=list)
    evidence: Optional[str] = None

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


def _all_unresolved(targets, reason) -> List[TruthResult]:
    return [TruthResult(t, None, "unresolved", [reason]) for t in targets]


def _derive_from_key(structure, polarity, correct, targets, combo, counts, explicit):
    """بخش (۲): فقط استنتاج از کلید. خروجی: دیکشنری target -> TruthResult."""
    keys = list(dict.fromkeys(correct))  # حذف تکرار با حفظ ترتیب
    out: Dict[str, TruthResult] = {}
    positive = polarity == "positive"

    def fail(reason):
        return {t: TruthResult(t, None, "unresolved", [reason]) for t in targets}

    if not keys:
        return fail("no_key")

    if structure in SINGLE_LIKE:
        if any(k not in targets for k in keys):
            return fail("key_not_in_options")
        for t in targets:
            in_key = t in keys
            out[t] = TruthResult(t, in_key if positive else (not in_key), "key_derived")
        return out

    if structure in COMBO_LIKE:
        if len(keys) != 1:
            return fail("multiple_keys_combo")
        opt = (combo or {}).get(keys[0])
        if opt is None:
            return fail("matching_ambiguous" if structure != "combo_items" else "combo_sets_missing")
        if opt.kind == "all":
            s = set(targets)
        elif opt.kind == "none":
            s = set()
        elif opt.kind == "set":
            s = set(opt.items)
            if not s <= set(targets):
                return fail("combo_item_unknown")
        else:
            return fail("combo_kind_unknown")
        for t in targets:
            out[t] = TruthResult(t, (t in s) if positive else (t not in s), "key_derived")
        return out

    if structure in COUNT_LIKE:
        if len(keys) != 1:
            return fail("multiple_keys_count")
        c = (counts or {}).get(keys[0])
        n = len(targets)
        if c is None:
            return fail("count_missing")
        if not (0 <= c <= n):
            return fail("count_out_of_range")
        # مقداری که شمرده می‌شود: مثبت ← «صحیح»ها، منفی ← «غلط»ها
        counted_value = positive
        if c == n:
            return {t: TruthResult(t, counted_value, "key_derived") for t in targets}
        if c == 0:
            return {t: TruthResult(t, not counted_value, "key_derived") for t in targets}
        # حالت میانی: فقط اگر احکام صریح اجازه‌ی استنتاج قطعی بدهند
        known = {t: explicit[t][0] for t in targets if explicit and t in explicit}
        counted_known = sum(1 for v in known.values() if v == counted_value)
        unknown = [t for t in targets if t not in known]
        if counted_known > c or counted_known + len(unknown) < c:
            res = fail("count_inconsistent_with_explanations")
            for r in res.values():
                r.review_reasons.append(CONFLICT)
            return res
        if unknown:
            # جای مواردی که حکم صریح دارند خالی می‌ماند؛ حکم صریح بعداً جایشان می‌نشیند
            placeholder = {t: TruthResult(t, None, "unresolved", []) for t in known}
            if counted_known == c:
                out = dict(placeholder)
                for t in unknown:
                    out[t] = TruthResult(t, not counted_value, "key_derived")
                return out
            if counted_known + len(unknown) == c:
                out = dict(placeholder)
                for t in unknown:
                    out[t] = TruthResult(t, counted_value, "key_derived")
                return out
        return fail("count_partial_unresolved")

    return fail("structure_unsupported")


def derive_truth(*, structure: str, polarity: Optional[str], answer_status: str,
                 correct: Iterable[str], targets: List[str],
                 combo: Optional[Dict[str, ComboOption]] = None,
                 counts: Optional[Dict[str, int]] = None,
                 explicit: Optional[Dict[str, Tuple[bool, str]]] = None) -> List[TruthResult]:
    correct = list(correct or [])
    explicit = explicit or {}

    if answer_status in ("annulled", "disputed"):
        return _all_unresolved(targets, f"answer_status_{answer_status}")
    if polarity not in ("positive", "negative"):
        return _all_unresolved(targets, "polarity_missing")

    if answer_status == "missing" or not correct:
        derived = {t: TruthResult(t, None, "unresolved", ["no_key"]) for t in targets}
    else:
        derived = _derive_from_key(structure, polarity, correct, targets, combo, counts, explicit)

    results: List[TruthResult] = []
    for t in targets:
        d = derived[t]
        e = explicit.get(t)
        if e is None:
            results.append(d)
            continue
        e_truth, e_evidence = e
        if d.truth is None:
            # حکم صریح جای null را می‌گیرد؛ دلایل غیر از «کلید ندارد» حفظ می‌شوند
            keep = [r for r in d.review_reasons if r == CONFLICT]
            results.append(TruthResult(t, e_truth, "explanation", keep, e_evidence))
        elif d.truth == e_truth:
            results.append(TruthResult(t, e_truth, "explanation", [], e_evidence))
        else:
            results.append(TruthResult(t, e_truth, "explanation", [CONFLICT], e_evidence))

    # سازگاری شمارشی: اگر همه‌ی موارد تعیین شده‌اند، تعدادشان باید با کلید بخواند
    if structure in COUNT_LIKE and polarity in ("positive", "negative") and correct and len(set(correct)) == 1:
        c = (counts or {}).get(correct[0])
        if c is not None and results and all(r.truth is not None for r in results):
            counted_value = polarity == "positive"
            if sum(1 for r in results if r.truth == counted_value) != c:
                for r in results:
                    if CONFLICT not in r.review_reasons:
                        r.review_reasons.append(CONFLICT)
    return results
