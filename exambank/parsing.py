# -*- coding: utf-8 -*-
"""تحلیل‌گرهای قاعده‌محور برای cross-check خروجی LLM (بدون LLM، بدون دانش زیستی)."""
import re

from .normalize import ZWNJ, dedupe_key, normalize_label, normalize_text
from .truth_rules import ComboOption

# ---------------------------------------------------------------- polarity
# «نا» قبل از «درست/صحیح» نباید با «درست» مثبت اشتباه شود.
_NEG_RE = re.compile(
    r"نادرست|ناصحیح|غلط|اشتباه|به\s*" + ZWNJ + r"?\s*جز|بجز|(?<![\w])نیست(?:ند)?(?![\w])|"
    r"نمی" + ZWNJ + r"?\s*باشند|نمی" + ZWNJ + r"?\s*باشد")
_POS_RE = re.compile(
    r"(?<!نا)(?:درست|صحیح)\s*(?:است|هست|می" + ZWNJ + r"?\s*باشد|می" + ZWNJ + r"?\s*باشند|اند|ند)"
    r"|(?<!نا)(?:درست|صحیح)" + ZWNJ + r"اند|(?<!نا)(?:درست|صحیح)(?:ی)?\s*(?:کدام|کدامند)")


_STRONG_NEG_RE = re.compile(r"به\s*" + ZWNJ + r"?\s*جز|بجز")


def polarity_hint(stem):
    """'negative' | 'positive' | None (نامشخص یا دارای هر دو نوع نشانه). فقط برای cross-check است.
    «به‌جز» نشانه‌ی قوی منفی است («همه… به‌جز … صحیح‌اند» یعنی کدام نادرست است)؛ در بدترین حالت
    یک پرچم بازبینی اضافی می‌دهد، نه یک truth غلط."""
    s = normalize_text(stem)
    if _STRONG_NEG_RE.search(s):
        return "negative"
    neg = bool(_NEG_RE.search(s))
    pos = bool(_POS_RE.search(s))
    if neg and not pos:
        return "negative"
    if pos and not neg:
        return "positive"
    return None


# ------------------------------------------------------------- گزینه‌ها
_NUM_WORDS = {"صفر": 0, "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8}
_FILLER = re.compile(r"[\s" + ZWNJ + r"]+")


def _clean(text):
    t = normalize_text(text)
    t = t.replace(ZWNJ, "")
    t = re.sub(r"[()\[\]{}.:؛;]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_count_option(text):
    """متن گزینه‌ی «چند مورد…» ← عدد، یا None اگر فقط عدد/کلمه‌ی عدد نیست."""
    t = _clean(text)
    t = re.sub(r"^(?:فقط|تنها)\s+", "", t)
    t = re.sub(r"\s+(?:مورد|عدد)$", "", t)
    if re.fullmatch(r"\d+", t):
        return int(t)
    if t in _NUM_WORDS:
        return _NUM_WORDS[t]
    if t in ("هیچ", "هیچکدام", "هیچ کدام", "هیچ مورد"):
        return 0
    return None


_ALL_RE = re.compile(r"^(?:فقط\s+)?(?:همه|تمام)(?:\s*ی)?(?:\s+(?:موارد|مورد|گزینه\s*ها))?$")
_NONE_RE = re.compile(r"^(?:هیچ\s*کدام|هیچ\s*یک|هیچ\s+(?:مورد|کدام))$")


def parse_combo_option(text, item_labels):
    """«الف و ج» / «فقط ب» / «همه» / «هیچ‌کدام» ← ComboOption، یا None اگر قطعی نیست."""
    t = _clean(text)
    t = re.sub(r"^(?:فقط|تنها)\s+", "", t)
    t = re.sub(r"^(?:موارد|مورد)\s+", "", t)
    if _NONE_RE.match(t):
        return ComboOption("none")
    if _ALL_RE.match(t):
        return ComboOption("all")
    labels = [normalize_label(x) for x in item_labels]
    parts = [p for p in re.split(r"\s*(?:،|,|-|–|/|\bو\b)\s*|\s+و\s+|\s+", t) if p and p != "و"]
    if not parts:
        return None
    parts = [normalize_label(p) for p in parts]
    if not all(p in labels for p in parts) or len(set(parts)) != len(parts):
        return None
    return ComboOption("set", tuple(sorted(parts, key=labels.index)))


def options_look_like(structure, options, item_labels=None):
    """بررسی ساده‌ی سازگاری structure با شکل گزینه‌ها (برای پرچم، نه برای تصمیم).
    True=سازگار، False=ناسازگار، None=قابل‌تشخیص نیست."""
    if structure == "count_items":
        return all(parse_count_option(o["text_original"] if "text_original" in o else o["text"]) is not None
                   for o in options)
    if structure == "combo_items" and item_labels:
        return all(parse_combo_option(o.get("text_original", o.get("text", "")), item_labels) is not None
                   for o in options)
    return None


# --------------------------------------------------------- واژه‌ی تازه
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def novel_token_ratio(candidate, *sources):
    """نسبت واژه‌های candidate که در هیچ‌کدام از sources نیامده‌اند (۰ تا ۱). برای کشف «افزودن واقعیت جدید»."""
    cand = _TOKEN_RE.findall(dedupe_key(candidate))
    if not cand:
        return 0.0
    pool = set()
    for s in sources:
        pool.update(_TOKEN_RE.findall(dedupe_key(s or "")))
    novel = [w for w in cand if w not in pool]
    return len(novel) / len(cand)


def contains_quote(haystack, quote):
    """آیا quote (نقل عین عبارت) در haystack هست؟ مقایسه با حذف نشانه‌گذاری، نیم‌فاصله و تفاوت ی/ک/ارقام."""
    q = dedupe_key(quote)
    return bool(q) and q in dedupe_key(haystack or "")
