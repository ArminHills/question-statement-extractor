# -*- coding: utf-8 -*-
"""schema خروجی extract.

دو لایه:
  • مدل‌های Pydantic (PageExtraction) برای response_schema و تولید schema/*.schema.json.
    بدون مقدار پیش‌فرض‌اند تا SDK آن‌ها را بپذیرد. pydantic در محیطی که این کد نوشته شد نصب نبود،
    پس این مدل‌ها آن‌جا اجرا نشده‌اند؛ tests/test_schemas_pydantic.py در محیط شما آن‌ها را بررسی می‌کند.
  • validate_page_payload: اعتبارسنج خالصِ پایتون که *مرجع واقعی پذیرش* است (تست‌شده، بدون وابستگی).
"""
import re
from typing import List, Literal, Optional

from .normalize import normalize_label, normalize_text

PAGE_ROLES = ("questions", "answer_key", "explanations", "mixed", "other")
FIGURE_KINDS = ("figure", "table", "graph", "diagram", "photo", "other")
ANSWER_ITEM_STATUSES = ("annulled", "disputed")

_PLACEHOLDER_RE = re.compile(r"هیچ\s*سوالی|no questions", re.I)


def build_pydantic_models():
    """import تنبل تا ماژول بدون pydantic هم import شود."""
    from pydantic import BaseModel

    class OptionItem(BaseModel):
        label: str
        text: str
        is_continuation: bool

    class QuestionItem(BaseModel):
        number: Optional[int]
        stem: str
        options: List[OptionItem]
        continues_from_previous_page: bool
        continues_on_next_page: bool

    class AnswerItem(BaseModel):
        number: Optional[int]
        correct: List[str]
        status: Optional[Literal["annulled", "disputed"]]
        explanation: Optional[str]
        continues_from_previous_page: bool
        continues_on_next_page: bool

    class FigureItem(BaseModel):
        kind: Literal["figure", "table", "graph", "diagram", "photo", "other"]
        description_fa: str
        labels_in_figure: List[str]
        box_2d: Optional[List[int]]
        question_numbers: List[int]

    class PageExtraction(BaseModel):
        page_role: Literal["questions", "answer_key", "explanations", "mixed", "other"]
        questions: List[QuestionItem]
        answers: List[AnswerItem]
        figures: List[FigureItem]

    return PageExtraction


def _to_int(v, where, errors):
    if v is None:
        return None
    if isinstance(v, bool):
        errors.append(f"{where}: شماره بولی است")
        return None
    if isinstance(v, int):
        return v
    try:
        return int(normalize_text(str(v)))
    except ValueError:
        errors.append(f"{where}: شماره‌ی نامعتبر {v!r}")
        return None


def validate_page_payload(data):
    """خروجی extract را بررسی و به شکل استاندارد درمی‌آورد. در صورت خطا ValueError با فهرست دلایل.
    ‌خالی/نامعتبر = شکست؛ «لیست خالیِ صریح» برای صفحه‌ی بدون سوال معتبر است."""
    errors = []
    if not isinstance(data, dict):
        raise ValueError("خروجی باید شیء JSON باشد")
    role = data.get("page_role")
    if role not in PAGE_ROLES:
        errors.append(f"page_role نامعتبر: {role!r}")
    for key in ("questions", "answers", "figures"):
        if not isinstance(data.get(key), list):
            errors.append(f"{key} باید لیست باشد (برای نبودن، لیست خالی بده)")
    if errors:
        raise ValueError("; ".join(errors))

    out = {"page_role": role, "questions": [], "answers": [], "figures": []}

    for i, q in enumerate(data["questions"]):
        w = f"questions[{i}]"
        if not isinstance(q, dict):
            errors.append(f"{w}: شیء نیست")
            continue
        stem = q.get("stem")
        if not isinstance(stem, str):
            errors.append(f"{w}.stem رشته نیست")
            continue
        if _PLACEHOLDER_RE.search(stem) and len(stem) < 60:
            errors.append(f"{w}: متن جایگزین به‌جای سوال («{stem[:30]}»)")
            continue
        opts = q.get("options")
        if not isinstance(opts, list):
            errors.append(f"{w}.options لیست نیست")
            continue
        cfp, cnp = bool(q.get("continues_from_previous_page")), bool(q.get("continues_on_next_page"))
        norm_opts = []
        for j, o in enumerate(opts):
            if not isinstance(o, dict) or not isinstance(o.get("text"), str) or not isinstance(o.get("label"), (str, int)):
                errors.append(f"{w}.options[{j}] نامعتبر")
                continue
            label = normalize_label(o["label"])
            if not label:
                errors.append(f"{w}.options[{j}]: برچسب خالی")
                continue
            if not o["text"].strip() and not o.get("is_continuation"):
                errors.append(f"{w}.options[{j}]: متن خالی")
                continue
            norm_opts.append({"label": label, "text": o["text"].strip(), "is_continuation": bool(o.get("is_continuation"))})
        if not stem.strip() and not norm_opts:
            errors.append(f"{w}: سوال کاملاً خالی")
            continue
        if not stem.strip() and not cfp:
            errors.append(f"{w}: صورت سوال خالی (و ادامه‌ی صفحه‌ی قبل نیست)")
            continue
        num = _to_int(q.get("number"), w, errors)
        if num is None and not cfp:
            errors.append(f"{w}: شماره ندارد و ادامه‌ی صفحه‌ی قبل هم نیست")
            continue
        if cfp and i != 0:
            errors.append(f"{w}: continues_from_previous_page فقط برای اولین آیتم مجاز است")
            continue
        out["questions"].append({"number": num, "stem": stem.strip(), "options": norm_opts,
                                 "continues_from_previous_page": cfp, "continues_on_next_page": cnp})

    for i, a in enumerate(data["answers"]):
        w = f"answers[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{w}: شیء نیست")
            continue
        corr = a.get("correct", [])
        if not isinstance(corr, list):
            errors.append(f"{w}.correct لیست نیست")
            continue
        status = a.get("status")
        if status not in (None,) + ANSWER_ITEM_STATUSES:
            errors.append(f"{w}.status نامعتبر: {status!r}")
            continue
        expl = a.get("explanation")
        if expl is not None and not isinstance(expl, str):
            errors.append(f"{w}.explanation رشته نیست")
            continue
        corr = [normalize_label(c) for c in corr if normalize_label(c)]
        if not corr and not status and not (expl or "").strip():
            errors.append(f"{w}: نه کلید دارد نه وضعیت نه توضیح")
            continue
        cfp = bool(a.get("continues_from_previous_page"))
        num = _to_int(a.get("number"), w, errors)
        if num is None and not cfp:
            errors.append(f"{w}: شماره ندارد")
            continue
        out["answers"].append({"number": num, "correct": corr, "status": status,
                               "explanation": expl.strip() if expl and expl.strip() else None,
                               "continues_from_previous_page": cfp,
                               "continues_on_next_page": bool(a.get("continues_on_next_page"))})

    for i, f in enumerate(data["figures"]):
        w = f"figures[{i}]"
        if not isinstance(f, dict) or f.get("kind") not in FIGURE_KINDS:
            errors.append(f"{w}: kind نامعتبر")
            continue
        box = f.get("box_2d")
        if box is not None and not (isinstance(box, list) and len(box) == 4
                                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)):
            errors.append(f"{w}.box_2d باید null یا ۴ عدد باشد")
            continue
        qn = f.get("question_numbers", [])
        if not isinstance(qn, list):
            errors.append(f"{w}.question_numbers لیست نیست")
            continue
        nums = []
        for v in qn:
            n = _to_int(v, w, errors)
            if n is not None:
                nums.append(n)
        labels = f.get("labels_in_figure", [])
        out["figures"].append({"kind": f["kind"], "description_fa": str(f.get("description_fa") or "").strip(),
                               "labels_in_figure": [str(x) for x in labels] if isinstance(labels, list) else [],
                               "box_2d": box, "question_numbers": nums})
    if errors:
        raise ValueError("; ".join(errors))
    return out
