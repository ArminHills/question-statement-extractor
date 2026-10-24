# -*- coding: utf-8 -*-
"""قرارداد رکوردهای خروجی (برای Frankenstein): منبع حقیقت واحد.

هر رکورد یک کلاس `Spec` با annotation‌های استاندارد پایتون است. از همین یک تعریف ساخته می‌شود:
  • validate(cls, data)          — اعتبارسنج خالص (بدون وابستگی؛ تست‌شده)
  • to_pydantic(cls)             — مدل Pydantic v2 (اگر pydantic نصب باشد؛ اعتبارسنجی مضاعف در validate stage)
  • json_schema(cls)             — فایل‌های schema/*.schema.json
  • type_repr / DOC              — جدول‌های SCHEMA.md
قواعد: فیلد بدون مقدار پیش‌فرض (حتی Optional) = الزامی (می‌تواند null باشد)؛ فیلد با مقدار پیش‌فرض = اختیاری.
فیلدهای ناشناخته مجازند (قرارداد قابل‌گسترش است)؛ حذف یا تغییر نام فیلد باید در DESIGN.md ثبت شود.
"""
import copy
from typing import Any, Dict, List, Literal, Optional, Union, get_args, get_origin, get_type_hints

from .constants import SCHEMA_VERSION

QUESTION_TYPES = ("textbook_recall", "counting", "analytical", "inferential", "figure_based", "graph_table_based",
                  "experiment_based", "calculation", "other")
STRUCTURES = ("single_best_statement", "completion", "count_items", "combo_items", "matching", "ordering",
              "calculation", "other")
ROUTES = ("statement", "problem", "mixed")
CONTEXTS = ("none", "figure", "table", "shared_scenario")
TRUTH_BASES = ("key_derived", "explanation", "unresolved", "manual")
REVIEW_STATUSES = ("pending", "approved", "edited", "rejected")
ANSWER_STATUSES = ("official", "annulled", "multiple_keys", "disputed", "missing")
ANSWER_SOURCES = ("answer_key", "explanation", "both", "none")


class Spec:
    """کلاس پایه‌ی قرارداد. DOC: توضیح هر فیلد برای SCHEMA.md."""
    DOC = {}


# ------------------------------------------------------------------ زیرساخت
def _fields(cls):
    return {k: v for k, v in get_type_hints(cls).items() if not k.startswith("_")}


def _is_spec(t):
    return isinstance(t, type) and issubclass(t, Spec)


def _check(t, v, path, errors):
    origin = get_origin(t)
    if t is Any:
        return
    if origin is Union:
        args = get_args(t)
        if v is None and type(None) in args:
            return
        subs = [a for a in args if a is not type(None)]
        first = None
        for a in subs:
            e = []
            _check(a, v, path, e)
            if not e:
                return
            first = first or e
        errors.extend(first or [f"{path}: مقدار نامعتبر"])
        return
    if origin is Literal:
        if v not in get_args(t):
            errors.append(f"{path}: مقدار {v!r} مجاز نیست (مجاز: {', '.join(map(str, get_args(t)))})")
        return
    if origin in (list, List):
        if not isinstance(v, list):
            errors.append(f"{path}: باید لیست باشد")
            return
        inner = (get_args(t) or (Any,))[0]
        for i, x in enumerate(v):
            _check(inner, x, f"{path}[{i}]", errors)
        return
    if origin in (dict, Dict):
        if not isinstance(v, dict):
            errors.append(f"{path}: باید شیء باشد")
        return
    if t is str:
        if not isinstance(v, str):
            errors.append(f"{path}: باید رشته باشد")
    elif t is bool:
        if not isinstance(v, bool):
            errors.append(f"{path}: باید بولی باشد")
    elif t is int:
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append(f"{path}: باید عدد صحیح باشد")
    elif t is float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f"{path}: باید عدد باشد")
    elif _is_spec(t):
        errors.extend(validate(t, v, path))
    else:
        errors.append(f"{path}: نوع پشتیبانی‌نشده {t}")


def validate(cls, data, path=""):
    """-> لیست خطاها (خالی = معتبر)."""
    if not isinstance(data, dict):
        return [f"{path or cls.__name__}: باید شیء باشد"]
    errors = []
    for name, t in _fields(cls).items():
        p = f"{path}.{name}" if path else name
        if name not in data:
            if not hasattr(cls, name):
                errors.append(f"{p}: الزامی است")
            continue
        _check(t, data[name], p, errors)
    return errors


# ------------------------------------------------------------------- Pydantic
def _to_pyd_type(t, cache):
    origin = get_origin(t)
    if origin is Union:
        args = tuple(_to_pyd_type(a, cache) for a in get_args(t))
        return Union[args]
    if origin in (list, List):
        return List[_to_pyd_type(get_args(t)[0], cache)]
    if _is_spec(t):
        return to_pydantic(t, cache)
    return t


def to_pydantic(cls, _cache=None):
    from pydantic import ConfigDict, Field, create_model
    cache = _cache if _cache is not None else {}
    if cls in cache:
        return cache[cls]
    fields = {}
    for name, t in _fields(cls).items():
        ann = _to_pyd_type(t, cache)
        if hasattr(cls, name):
            d = getattr(cls, name)
            fields[name] = (ann, Field(default_factory=(lambda d=d: copy.deepcopy(d))) if isinstance(d, (list, dict)) else d)
        else:
            fields[name] = (ann, ...)
    model = create_model(cls.__name__, __config__=ConfigDict(extra="allow"), **fields)
    cache[cls] = model
    return model


# ---------------------------------------------------------------- JSON Schema
def _schema_type(t, defs):
    origin = get_origin(t)
    if t is Any:
        return {}
    if origin is Union:
        return {"anyOf": [_schema_type(a, defs) if a is not type(None) else {"type": "null"} for a in get_args(t)]}
    if origin is Literal:
        return {"enum": list(get_args(t))}
    if origin in (list, List):
        return {"type": "array", "items": _schema_type((get_args(t) or (Any,))[0], defs)}
    if origin in (dict, Dict):
        return {"type": "object"}
    if t is str:
        return {"type": "string"}
    if t is bool:
        return {"type": "boolean"}
    if t is int:
        return {"type": "integer"}
    if t is float:
        return {"type": "number"}
    if _is_spec(t):
        if t.__name__ not in defs:
            defs[t.__name__] = None  # جلوگیری از بازگشت بی‌پایان
            defs[t.__name__] = _object_schema(t, defs)
        return {"$ref": f"#/$defs/{t.__name__}"}
    raise TypeError(f"نوع پشتیبانی‌نشده: {t}")


def _object_schema(cls, defs):
    props, required = {}, []
    for name, t in _fields(cls).items():
        s = dict(_schema_type(t, defs))
        if name in cls.DOC:
            s["description"] = cls.DOC[name]
        props[name] = s
        if not hasattr(cls, name):
            required.append(name)
    out = {"type": "object", "properties": props, "required": required, "additionalProperties": True}
    if cls.__doc__ and cls.__doc__.strip():
        out["description"] = cls.__doc__.strip().splitlines()[0]
    return out


def json_schema(cls):
    defs = {}
    top = _object_schema(cls, defs)
    top["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    top["title"] = cls.__name__
    if defs:
        top["$defs"] = {k: v for k, v in sorted(defs.items())}
    return top


def type_repr(t):
    origin = get_origin(t)
    if origin is Union:
        args = [a for a in get_args(t) if a is not type(None)]
        s = " | ".join(type_repr(a) for a in args)
        return s + " | null" if type(None) in get_args(t) else s
    if origin is Literal:
        return " \\| ".join(f"`{a}`" for a in get_args(t))
    if origin in (list, List):
        return f"list[{type_repr((get_args(t) or (Any,))[0])}]"
    if _is_spec(t):
        return f"{t.__name__}"
    return getattr(t, "__name__", str(t))


# =================================================================== رکوردها
class Pages(Spec):
    """شماره‌ی صفحه‌های PDF منبع."""
    question: List[int]
    answer: List[int]
    DOC = {"question": "صفحه‌هایی که صورت سوال در آن‌هاست (۱-مبنا، نسبت به PDF)",
           "answer": "صفحه‌هایی که کلید/پاسخ تشریحی در آن‌هاست (ممکن است خالی باشد)"}


class Source(Spec):
    """ردیابی منبع: هر رکورد باید تا صفحه‌ی PDF قابل‌ردیابی باشد."""
    exam_id: str
    year_jalali: Optional[int]
    group: Optional[str]
    question_no: int
    option_label: Optional[str]
    pages: Pages
    DOC = {"exam_id": "شناسه‌ی آزمون (از manifest/--exam-id)", "year_jalali": "سال شمسی آزمون؛ null اگر داده نشده (پرچم source_missing_year)",
           "group": "گروه آزمون؛ null اگر داده نشده (پرچم source_missing_group)", "question_no": "شماره‌ی سوال در آزمون",
           "option_label": "برچسب گزینه (single/completion) یا مورد (الف..د)؛ برای مسئله null",
           "pages": "صفحه‌های منبع"}


class Dedupe(Spec):
    """نتیجه‌ی حذف تکرار؛ رکورد هرگز پاک نمی‌شود."""
    canonical_id: str
    occurrences: int
    years: List[int] = []
    match: Optional[str] = None
    DOC = {"canonical_id": "شناسه‌ی نماینده‌ی خوشه (قدیمی‌ترین رخداد)", "occurrences": "تعداد رخدادها در همه‌ی سال‌ها/آزمون‌ها",
           "years": "سال‌های شمسی رخدادها", "match": "`exact` (متن نرمال‌شده یکسان) | `fuzzy` (شباهت تقریبی) | null (یکتا)"}


class ReviewInfo(Spec):
    """سابقه‌ی بازبینی انسانی (فقط وقتی بازبین رکورد را تغییر داده/تأیید کرده)."""
    at: Optional[str] = None
    note: Optional[str] = None
    previous_truth: Optional[bool] = None
    previous_basis: Optional[str] = None


class Statement(Spec):
    """یک گزاره‌ی مستقل با برچسب صحیح/غلط (بانک گزاره‌ها)."""
    schema_version: str
    statement_id: str
    question_id: str
    text_original: str
    text_standalone: str
    truth: Optional[bool]
    truth_basis: Literal["key_derived", "explanation", "unresolved", "manual"]
    truth_evidence: Optional[str] = None
    why_false: Optional[str]
    distractor_type: Optional[str] = None
    is_atomic: bool
    standalone: bool
    context_dependency: Literal["none", "figure", "table", "shared_scenario"]
    context_ref: Optional[str]
    topic_ids: List[str]
    question_type: Literal["textbook_recall", "counting", "analytical", "inferential", "figure_based",
                           "graph_table_based", "experiment_based", "calculation", "other"]
    structure: Literal["single_best_statement", "completion", "count_items", "combo_items", "matching",
                       "ordering", "calculation", "other"]
    polarity: Optional[Literal["positive", "negative"]] = None
    parent_statement_id: Optional[str] = None
    source: Source
    dedupe: Dedupe
    needs_review: bool
    review_reasons: List[str]
    review_status: Literal["pending", "approved", "edited", "rejected"]
    llm_confidence: float
    review: Optional[ReviewInfo] = None
    DOC = {
        "statement_id": "شناسه‌ی پایدار: `<question_id>_o<برچسب گزینه>` یا `<question_id>_i<شماره‌ی مورد>`؛ جزء تجزیه‌شده `…_p<k>`",
        "text_original": "عین متن (بدون نرمال‌سازی)", "text_standalone": "جمله‌ی کامل و مستقل (ساخته‌ی LLM، فقط برای خوانایی؛ مبنای truth نیست)",
        "truth": "true | false | null. **فقط** از پاسخنامه یا بازبین انسانی؛ هرگز از دانش مدل",
        "truth_basis": "`key_derived` (استنتاج قطعی از کلید) | `explanation` (حکم صریح پاسخ تشریحی) | `unresolved` (truth=null) | `manual` (تعیین/تأیید بازبین انسانی)",
        "truth_evidence": "نقل عین از پاسخ تشریحی وقتی truth_basis=explanation",
        "why_false": "عین توضیح پاسخنامه برای گزاره‌ی غلط، یا null", "distractor_type": "نوع گمراه‌کننده، فقط وقتی از پاسخنامه آشکار است",
        "is_atomic": "false یعنی گزینه مرکب است و تجزیه نشده (یا اجزایش با parent_statement_id آمده‌اند)",
        "standalone": "false یعنی بدون شکل/جدول/سناریو معنا ندارد", "context_ref": "figure_id شکل یا `shared_scenario` وقتی standalone=false",
        "topic_ids": "اولی مبحث اصلی، حداکثر دو فرعی؛ فقط شناسه‌های taxonomy/biology.json",
        "polarity": "polarity صورت سوال (positive|negative)؛ نفیِ داخل خود گزاره جزء گزاره است",
        "parent_statement_id": "برای جزءهای تجزیه‌شده: شناسه‌ی گزاره‌ی مرکب والد",
        "source": "ردیابی منبع تا صفحه‌ی PDF (Source)", "dedupe": "نتیجه‌ی حذف تکرار بین سال‌ها (Dedupe)",
        "review": "سابقه‌ی بازبینی انسانی (فقط وقتی بازبین دست زده)",
        "needs_review": "true اگر هر پرچم قاعده‌محوری باز است. llm_confidence هرگز به‌تنهایی آن را false نمی‌کند",
        "review_reasons": "کدهای قاعده‌محور (در SCHEMA.md فهرست شده‌اند)", "review_status": "pending | approved | edited | rejected (rejected از statements.jsonl حذف می‌شود)",
        "llm_confidence": "کمکی؛ هرگز مبنای تأیید نیست"}


class ProblemOption(Spec):
    label: str
    text_original: str
    text: str


class Slot(Spec):
    name: str
    value: str
    role: Literal["given", "asked", "constant"]


class Parametrization(Spec):
    """واریانت‌پذیری مسئله (best-effort، فقط از متن خود مسئله)."""
    is_candidate: bool
    slots: List[Slot]


class Problem(Spec):
    """مسئله‌ای که با روش خاص حل می‌شود (بانک مسئله‌ها)."""
    schema_version: str
    problem_id: str
    question_id: str
    stem_original: str
    stem: str
    options: List[ProblemOption]
    correct: List[str]
    answer_status: Literal["official", "annulled", "multiple_keys", "disputed", "missing"]
    solution_text: Optional[str]
    problem_class: str
    givens: List[str]
    asked: Optional[str]
    figure_refs: List[str]
    topic_ids: List[str]
    question_type: Literal["textbook_recall", "counting", "analytical", "inferential", "figure_based",
                           "graph_table_based", "experiment_based", "calculation", "other"]
    parametrization: Optional[Parametrization]
    source: Source
    dedupe: Optional[Dedupe] = None
    needs_review: bool
    review_reasons: List[str]
    review_status: Literal["pending", "approved", "edited", "rejected"]
    llm_confidence: float
    review: Optional[ReviewInfo] = None
    DOC = {"problem_id": "`<question_id>_prob`", "solution_text": "عین پاسخ تشریحی یا null",
           "problem_class": "genetics_cross، pedigree، probability_counting، dna_rna_calc، cell_division_count، physiology_calc، graph_data_interpretation، experiment_inference، logic_inference، other؛ کلاس تازه با پرچم new_problem_class",
           "givens": "عبارت‌های معلوم، عیناً از صورت سوال", "asked": "آنچه خواسته شده، عیناً از صورت سوال",
           "figure_refs": "figure_idهای وابسته", "parametrization": "slotها عیناً از متن مسئله"}


class QOption(Spec):
    label: str
    text_original: str
    text: str


class Exam(Spec):
    exam_id: str
    year_jalali: Optional[int]
    group: Optional[str]
    session: Optional[str]
    textbook_edition: Optional[str]


class Question(Spec):
    """رکورد کامل سوال؛ منبع حقیقت برای ردیابی."""
    schema_version: str
    question_id: str
    exam: Exam
    number: int
    stem_original: str
    stem: str
    options: List[QOption]
    correct: List[str]
    answer_status: Literal["official", "annulled", "multiple_keys", "disputed", "missing"]
    answer_source: Literal["answer_key", "explanation", "both", "none"]
    explanation_original: Optional[str]
    explanation: Optional[str]
    question_type: Literal["textbook_recall", "counting", "analytical", "inferential", "figure_based",
                           "graph_table_based", "experiment_based", "calculation", "other"]
    structure: Literal["single_best_statement", "completion", "count_items", "combo_items", "matching",
                       "ordering", "calculation", "other"]
    polarity: Optional[Literal["positive", "negative"]]
    polarity_evidence: Optional[str]
    route: Literal["statement", "problem", "mixed"]
    topic_ids: List[str]
    figure_ids: List[str]
    context_dependency: Literal["none", "figure", "table", "shared_scenario"]
    pages: Pages
    review_reasons: List[str]
    llm_confidence: float
    DOC = {"correct": "برچسب گزینه‌های کلید (چند عضو = چند کلید رسمی)", "answer_status": "وضعیت کلید",
           "answer_source": "کلید از جدول کلید، پاسخ تشریحی یا هر دو آمده",
           "explanation_original": "عین پاسخ تشریحی", "route": "statement | problem | mixed (mixed در هر دو بانک است)",
           "review_reasons": "پرچم‌های سطح سوال (classify/assemble)"}


RECORD_CLASSES = {"statement": Statement, "problem": Problem, "question": Question}
