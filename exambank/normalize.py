# -*- coding: utf-8 -*-
"""نرمال‌سازی متن فارسی و نمادهای زیستی. قرارداد کامل در SCHEMA.md ثبت می‌شود.

قرارداد نمادها:
  • بالا/پایین‌نویس فقط شامل رقم و + و − ← یونیکد:  Na⁺  Ca²⁺  CO₂  H₂O
  • بالا/پایین‌نویس شامل حرف (ژنوتیپ‌ها و ...) ← همیشه با آکولاد:  X^{A} X^{a}  I^{A}i
    (چون برای همه‌ی حروف بالانویس یونیکد وجود ندارد، یک قرارداد یکدست انتخاب شده)
  • فرمول‌ها و یون‌ها فقط از فهرست سفید زیر تبدیل می‌شوند تا اصطلاح دیگری خراب نشود.
فیلدهای *_original هرگز از این توابع عبور نمی‌کنند.
"""
import hashlib
import re
import unicodedata

ZWNJ = "\u200c"
_FA = "\u0621-\u063A\u0641-\u064A\u067E\u0686\u0698\u06A9\u06AF\u06CC\u06C0"  # حروف فارسی/عربی

# ---- نگاشت نویسه‌ها --------------------------------------------------------
_CHAR_MAP = {
    "ي": "ی", "ى": "ی", "ې": "ی",
    "ك": "ک",
    "ة": "ه",
    "ـ": None,  # کشیده
    "٫": ".", "٬": ",", "٪": "%",
    "\u00a0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ", "\u202f": " ", "\u3000": " ",
}
for _i in range(10):
    _CHAR_MAP[chr(0x06F0 + _i)] = str(_i)   # ارقام فارسی
    _CHAR_MAP[chr(0x0660 + _i)] = str(_i)   # ارقام عربی-هندی
# کاراکترهای کنترلی جهت‌نما و نامرئی (به‌جز ZWNJ)
for _c in "\u200b\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff\u00ad":
    _CHAR_MAP[_c] = None
# اعراب
for _cp in list(range(0x064B, 0x0660)) + [0x0670]:
    _CHAR_MAP[chr(_cp)] = None
_TRANSLATE = {ord(k): v for k, v in _CHAR_MAP.items()}

_SUP = str.maketrans("0123456789+-−", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻")
_SUB = str.maketrans("0123456789+-−", "₀₁₂₃₄₅₆₇₈₉₊₋₋")
_SUP_OK = set("0123456789+-−")


def _presentation_forms(text):
    """فقط شکل‌های نمایشی عربی (ﻻ، ﻓ ...) را NFKC می‌کنیم؛ NFKC کامل بالانویس ² را به 2 تبدیل می‌کرد."""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


# ---- نمادهای زیستی ---------------------------------------------------------
_CARET_RE = re.compile(r"\^\{([^{}]*)\}|\^(\d*[+\-−]|[A-Za-z0-9]+)")
_UNDER_RE = re.compile(r"_\{([^{}]*)\}|_([0-9]+|[A-Za-z])")


def _sup(m):
    tok = m.group(1) if m.group(1) is not None else m.group(2)
    if tok and all(c in _SUP_OK for c in tok):
        return tok.translate(_SUP)
    return "^{" + tok + "}"


def _sub(m):
    tok = m.group(1) if m.group(1) is not None else m.group(2)
    if tok and all(c in _SUP_OK for c in tok):
        return tok.translate(_SUB)
    return "_{" + tok + "}"


_FORMULAS = {
    "C6H12O6": "C₆H₁₂O₆", "H2CO3": "H₂CO₃", "H2O2": "H₂O₂", "H2O": "H₂O",
    "CO2": "CO₂", "SO2": "SO₂", "NO2": "NO₂", "CH4": "CH₄", "NH3": "NH₃",
    "O2": "O₂", "N2": "N₂", "H2": "H₂",
}
_FORMULA_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(sorted(map(re.escape, _FORMULAS), key=len, reverse=True)) + r")(?![A-Za-z0-9])"
)

_POLY = {"HCO3": "HCO₃", "PO4": "PO₄", "SO4": "SO₄", "NO3": "NO₃", "NH4": "NH₄"}
_CATIONS = ["Na", "K", "Ca", "Mg", "Fe", "Cu", "Zn", "Li", "Mn", "NH4", "H"]
_ANIONS = ["Cl", "HCO3", "PO4", "SO4", "NO3", "OH"]
_CATION_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(_CATIONS, key=len, reverse=True))
                        + r")(\d?)\+(?![A-Za-z0-9+])")
_ANION_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(_ANIONS, key=len, reverse=True))
                       + r")(\d?)[\-−](?![A-Za-z0-9\-−])")


def _ion(sign):
    def f(m):
        name = _POLY.get(m.group(1), m.group(1))
        charge = (m.group(2) or "") + sign
        return name + charge.translate(_SUP)
    return f


def normalize_bio_notation(text):
    text = _CARET_RE.sub(_sup, text)
    text = _UNDER_RE.sub(_sub, text)
    text = _FORMULA_RE.sub(lambda m: _FORMULAS[m.group(1)], text)
    text = _CATION_RE.sub(_ion("+"), text)
    text = _ANION_RE.sub(_ion("-"), text)
    return text


# ---- نیم‌فاصله -------------------------------------------------------------
_MI_RE = re.compile(r"(?<![" + _FA + r"])(ن?می) +(?=[" + _FA + r"])")
_SUFFIX_RE = re.compile(r"(?<=[" + _FA + r"]) +(ها|های|هایی|ترین|تری|تر)(?![" + _FA + r"])")
_HE_YE_RE = re.compile(r"(?<=[هۀ]) +ی(?![" + _FA + r"])")
_ZWNJ_SPACE_RE = re.compile(r"[ \t]*" + ZWNJ + r"[ \t]*")
_ZWNJ_MULTI_RE = re.compile(ZWNJ + "{2,}")
_ZWNJ_BAD_RE = re.compile(r"(?<![" + _FA + r"])" + ZWNJ + "|" + ZWNJ + r"(?![" + _FA + r"])")


def fix_zwnj(text):
    text = _MI_RE.sub(lambda m: m.group(1) + ZWNJ, text)
    text = _SUFFIX_RE.sub(lambda m: ZWNJ + m.group(1), text)
    text = _HE_YE_RE.sub(ZWNJ + "ی", text)
    text = _ZWNJ_SPACE_RE.sub(ZWNJ, text)
    text = _ZWNJ_MULTI_RE.sub(ZWNJ, text)
    # نیم‌فاصله فقط بین دو حرف فارسی معنا دارد
    text = _ZWNJ_BAD_RE.sub("", text)
    return text


def normalize_text(text, keep_newlines=False):
    """متن نرمال‌شده. ورودی None ← رشته‌ی خالی."""
    if not text:
        return ""
    text = _presentation_forms(text)
    text = text.translate(_TRANSLATE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = normalize_bio_notation(text)
    if keep_newlines:
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
        text = "\n".join(ln for ln in lines)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return "\n".join(fix_zwnj(ln) for ln in text.split("\n"))
    text = re.sub(r"\s+", " ", text).strip()
    return fix_zwnj(text)


def normalize_label(label):
    """برچسب گزینه/مورد: ارقام ASCII، حذف ). و فاصله."""
    if label is None:
        return ""
    s = normalize_text(str(label))
    return s.strip().strip(").(:.-").strip()


_PUNCT_RE = re.compile(r"[^\w\u2070-\u209f\u00b2\u00b3\u00b9^{}]+", re.UNICODE)


def dedupe_key(text):
    """کلید مقایسه برای حذف تکرار: متن نرمال‌شده بدون نشانه‌گذاری و نیم‌فاصله."""
    t = normalize_text(text).replace(ZWNJ, "")
    t = _PUNCT_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def text_hash(text):
    return hashlib.sha256(dedupe_key(text).encode("utf-8")).hexdigest()
