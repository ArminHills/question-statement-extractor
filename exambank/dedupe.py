# -*- coding: utf-8 -*-
"""حذف تکرار بین سال‌ها/آزمون‌ها. رکوردها هرگز پاک نمی‌شوند؛ canonical_id و occurrences ثبت می‌شود.

مراحل: ۱) هش متن نرمال‌شده (exact)  ۲) شباهت تقریبی (rapidfuzz اگر نصب باشد، وگرنه difflib) بین نماینده‌ها.
نگهبان‌ها: شباهت تقریبی روی جمله‌های فارسی خطرناک است («هسته دارد» ≈ «هسته ندارد»)، پس دو گزاره‌ی متفاوت در
  • نفی (دارد/ندارد، است/نیست، فاقد، بدون، غیر ...)
  • عدد (۲ در برابر ۳)
  • ترتیب واژه‌ها با مجموعه‌ی واژه‌ی یکسان («الف از ب بزرگ‌تر است» / «ب از الف بزرگ‌تر است»)
هرگز تقریباً-تکراری حساب نمی‌شوند. گزاره‌ی وابسته به شکل/سناریو (standalone=false) خوشه نمی‌شود.
اگر دو گزاره‌ی مشابه truth متضاد داشته باشند، گزاره‌های pending با دلیل cross_year_truth_conflict پرچم می‌خورند."""
import re
from collections import defaultdict
from difflib import SequenceMatcher

from .normalize import dedupe_key, text_hash

CONFLICT = "cross_year_truth_conflict"
DERIVED_REASONS = (CONFLICT,)

_NEG_WORDS = {"نیست", "نیستند", "ندارد", "ندارند", "نمی", "نه", "بدون", "فاقد", "غیر", "هیچ", "نشده", "نشود", "نشوند",
              "نخواهد", "نبود", "نبودند", "نباشد", "نباید", "نتواند", "نا"}
_DIGIT = re.compile(r"\d")


def similarity(a, b):
    """۰ تا ۱۰۰. rapidfuzz.fuzz.ratio در صورت نصب، وگرنه difflib (نمره‌ها کمی متفاوت‌اند)."""
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(a, b))
    except ImportError:
        return SequenceMatcher(None, a, b, autojunk=False).ratio() * 100


def _tokens(text):
    return re.findall(r"\w+", dedupe_key(text), flags=re.UNICODE)


def _negation_differs(ta, tb):
    d1, d2 = set(ta) - set(tb), set(tb) - set(ta)
    if (d1 | d2) & _NEG_WORDS:
        return True
    for a in d1:
        for b in d2:
            if a == "ن" + b or b == "ن" + a or a == "نا" + b or b == "نا" + a or a == "نمی" + b or b == "نمی" + a:
                return True
    return False


def is_near_duplicate(text_a, text_b, threshold):
    ta, tb = _tokens(text_a), _tokens(text_b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if sorted(ta) == sorted(tb):
        return False
    diff = set(ta) ^ set(tb)
    if any(_DIGIT.search(w) for w in diff):
        return False
    if _negation_differs(ta, tb):
        return False
    return similarity(" ".join(ta), " ".join(tb)) >= threshold


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _order_key(rec, id_field):
    s = rec.get("source", {})
    return (s.get("year_jalali") if s.get("year_jalali") is not None else 10 ** 9, str(s.get("exam_id", "")),
            s.get("question_no") or 0, str(s.get("option_label") or ""), rec[id_field])


def cluster_records(records, id_field, text_fn, threshold=90, eligible=lambda r: True, max_df=40):
    """-> {record_id: {"canonical_id","occurrences","years","match"}} برای همه‌ی رکوردها."""
    idx = [i for i, r in enumerate(records) if eligible(r)]
    uf = _UF(len(records))
    exact = defaultdict(list)
    for i in idx:
        exact[text_hash(text_fn(records[i]))].append(i)
    reps = []
    for members in exact.values():
        for m in members[1:]:
            uf.union(members[0], m)
        reps.append(members[0])
    fuzzy_edges = set()
    # شاخص معکوس برای تولید نامزد (نه O(n²))
    inv = defaultdict(list)
    toks = {i: set(_tokens(text_fn(records[i]))) for i in reps}
    for i in reps:
        for w in toks[i]:
            inv[w].append(i)
    for i in reps:
        seen = defaultdict(int)
        for w in toks[i]:
            if len(inv[w]) <= max_df:
                for j in inv[w]:
                    if j > i:
                        seen[j] += 1
        for j, shared in seen.items():
            union = len(toks[i] | toks[j])
            if union and shared / union >= 0.5 and is_near_duplicate(text_fn(records[i]), text_fn(records[j]), threshold):
                uf.union(i, j)
                fuzzy_edges.add(j)
                fuzzy_edges.add(i)
    groups = defaultdict(list)
    for i in idx:
        groups[uf.find(i)].append(i)
    out = {}
    for members in groups.values():
        recs = sorted((records[i] for i in members), key=lambda r: _order_key(r, id_field))
        canonical = recs[0][id_field]
        years = sorted({r["source"]["year_jalali"] for r in recs if r.get("source", {}).get("year_jalali") is not None})
        hashes = {text_hash(text_fn(r)) for r in recs}
        kind = None if len(recs) == 1 else ("exact" if len(hashes) == 1 else "fuzzy")
        for r in recs:
            out[r[id_field]] = {"canonical_id": canonical, "occurrences": len(recs), "years": years, "match": kind}
    for i, r in enumerate(records):
        if i not in set(idx):
            out[r[id_field]] = {"canonical_id": r[id_field], "occurrences": 1, "years": [], "match": None}
    return out


def _clean_reasons(rec):
    rec["review_reasons"] = [x for x in rec.get("review_reasons", []) if x not in DERIVED_REASONS]


def comparable(s):
    """گزاره‌ای که قابل مقایسه با سوال‌های دیگر است: مستقل باشد و، جز در single_best_statement (که متن گزینه
    خودش یک جمله‌ی کامل است)، text_standalone واقعاً از text_original متفاوت (= دارای زمینه‌ی صورت سوال) باشد؛
    وگرنه «مورد یک» از دو سوال مختلف یکی حساب می‌شد."""
    if not s["standalone"] or s["review_status"] == "rejected":
        return False
    if s["structure"] != "single_best_statement" and dedupe_key(s["text_standalone"]) == dedupe_key(s["text_original"]):
        return False
    return True


def dedupe_statements(statements, threshold=90):
    """بر جای رکوردها: dedupe را پر و پرچم تعارض truth را اعمال می‌کند. -> آمار."""
    for s in statements:
        _clean_reasons(s)
    live = [s for s in statements if s["review_status"] != "rejected"]
    info = cluster_records(statements, "statement_id", lambda r: r["text_standalone"], threshold,
                           eligible=comparable)
    by_cluster = defaultdict(list)
    for s in statements:
        s["dedupe"] = info[s["statement_id"]]
        by_cluster[s["dedupe"]["canonical_id"]].append(s)
    conflicts = 0
    for members in by_cluster.values():
        live_m = [m for m in members if m["review_status"] != "rejected" and m["truth"] is not None]
        if len({m["truth"] for m in live_m}) > 1:
            conflicts += 1
            for m in live_m:
                if m["review_status"] == "pending" and CONFLICT not in m["review_reasons"]:
                    m["review_reasons"].append(CONFLICT)
    for s in statements:
        s["review_reasons"] = sorted(set(s["review_reasons"]))
        s["needs_review"] = bool(s["review_reasons"]) if s["review_status"] == "pending" else False
    clusters = {s["dedupe"]["canonical_id"] for s in live}
    return {"statements": len(statements), "clusters": len(clusters),
            "duplicated_statements": sum(1 for s in live if s["dedupe"]["occurrences"] > 1),
            "truth_conflict_clusters": conflicts}


def dedupe_problems(problems, threshold=90):
    info = cluster_records(problems, "problem_id", lambda r: r["stem_original"], threshold,
                           eligible=lambda r: r["review_status"] != "rejected")
    for p in problems:
        p["dedupe"] = info[p["problem_id"]]
        p["review_reasons"] = sorted(set(p["review_reasons"]))
        p["needs_review"] = bool(p["review_reasons"]) if p["review_status"] == "pending" else False
    return {"problems": len(problems), "duplicated_problems": sum(1 for p in problems if p["dedupe"]["occurrences"] > 1)}
