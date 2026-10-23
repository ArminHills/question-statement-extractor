# -*- coding: utf-8 -*-
"""taxonomy/biology.json: فهرست فصل/گفتار کتاب‌های درسی، فقط از صفحه‌های فهرستی که کاربر می‌دهد.
هیچ فصلی از حافظه‌ی مدل یا من نوشته نمی‌شود. شناسه‌ها پایدارند و فایل با دست قابل ویرایش است.

قالب:
{"version": 1, "books": [{"book_id": "bio10", "title": "...", "grade": 10, "source": "...",
   "chapters": [{"chapter_id": "bio10-ch01", "number": 1, "title": "...",
                 "sections": [{"section_id": "bio10-ch01-s01", "number": 1, "title": "..."}]}]}]}
topic_id معتبر = section_id، یا chapter_id برای فصلی که گفتار ندارد."""
from pathlib import Path

from .normalize import normalize_text
from .utils import read_json, write_json

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "biology.json"


class TaxonomyMissing(Exception):
    pass


class TaxonomyInvalid(Exception):
    pass


def load_taxonomy(path=None):
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        raise TaxonomyMissing(
            f"فایل taxonomy پیدا نشد: {p}\n"
            "برای ساخت آن، صفحه‌های فهرست مطالب کتاب‌های زیست دهم، یازدهم و دوازدهم را بدهید و برای هر کتاب اجرا کنید:\n"
            "  python -m exambank build-taxonomy -n tax -i <فهرست.pdf> -p <صفحه‌ها> -o <پوشه> "
            "--book-id bio10 --grade 10 --book-title \"زیست‌شناسی دهم\"\n"
            "(یا فایل را با دست در قالب مستند در taxonomy/biology.json بسازید.)")
    tax = read_json(p)
    if not isinstance(tax, dict):
        raise TaxonomyInvalid(f"taxonomy خوانده نشد (JSON نامعتبر): {p}")
    validate_taxonomy(tax)
    return tax


def validate_taxonomy(tax):
    errors, seen = [], set()
    books = tax.get("books")
    if not isinstance(books, list) or not books:
        raise TaxonomyInvalid("taxonomy باید حداقل یک کتاب داشته باشد")
    for b in books:
        if not b.get("book_id") or not b.get("chapters"):
            errors.append(f"کتاب نامعتبر: {b.get('book_id')!r}")
            continue
        for ch in b["chapters"]:
            ids = [ch.get("chapter_id")] + [s.get("section_id") for s in ch.get("sections", [])]
            for i in ids:
                if not i:
                    errors.append(f"شناسه‌ی خالی در {b['book_id']}")
                elif i in seen:
                    errors.append(f"شناسه‌ی تکراری: {i}")
                seen.add(i)
            if not str(ch.get("title", "")).strip():
                errors.append(f"فصل بدون عنوان: {ch.get('chapter_id')}")
    if errors:
        raise TaxonomyInvalid("; ".join(errors))


def topic_ids(tax):
    out = []
    for b in tax["books"]:
        for ch in b["chapters"]:
            secs = ch.get("sections") or []
            if secs:
                out.extend(s["section_id"] for s in secs)
            else:
                out.append(ch["chapter_id"])
    return out


def describe_for_prompt(tax):
    """فهرست فشرده برای prompt: «شناسه | کتاب > فصل > گفتار»."""
    lines = []
    for b in tax["books"]:
        for ch in b["chapters"]:
            secs = ch.get("sections") or []
            if secs:
                for s in secs:
                    lines.append(f"{s['section_id']} | {b['title']} > فصل {ch['number']}: {ch['title']} > گفتار {s['number']}: {s['title']}")
            else:
                lines.append(f"{ch['chapter_id']} | {b['title']} > فصل {ch['number']}: {ch['title']}")
    return "\n".join(lines)


def merge_toc_pages(book_id, title, grade, page_results, source=""):
    """page_results: لیست خروجی LLM هر صفحه‌ی فهرست: [{"chapter_number","chapter_title","sections":[{"number","title"}]}].
    فصل‌های هم‌شماره (ادامه در صفحه‌ی بعد) ادغام می‌شوند. شناسه‌ها از شماره ساخته می‌شوند."""
    chapters = {}
    for page in page_results:
        for ch in page:
            n = int(ch["chapter_number"])
            c = chapters.setdefault(n, {"number": n, "title": normalize_text(ch.get("chapter_title") or ""),
                                        "sections": {}})
            if not c["title"]:
                c["title"] = normalize_text(ch.get("chapter_title") or "")
            for s in ch.get("sections", []):
                m = int(s["number"])
                c["sections"].setdefault(m, normalize_text(s["title"]))
    out = []
    for n in sorted(chapters):
        c = chapters[n]
        cid = f"{book_id}-ch{n:02d}"
        out.append({"chapter_id": cid, "number": n, "title": c["title"],
                    "sections": [{"section_id": f"{cid}-s{m:02d}", "number": m, "title": t}
                                 for m, t in sorted(c["sections"].items())]})
    return {"book_id": book_id, "title": normalize_text(title), "grade": grade, "source": source, "chapters": out}


def save_book(path, book, force=False):
    p = Path(path)
    tax = read_json(p, None) or {"version": 0, "books": []}
    existing = [i for i, b in enumerate(tax["books"]) if b["book_id"] == book["book_id"]]
    if existing and not force:
        raise TaxonomyInvalid(f"کتاب {book['book_id']} از قبل در taxonomy هست (برای جایگزینی --force بدهید؛ "
                              "ویرایش‌های دستی آن از بین می‌رود).")
    if existing:
        tax["books"][existing[0]] = book
    else:
        tax["books"].append(book)
    tax["version"] = int(tax.get("version", 0)) + 1
    validate_taxonomy(tax)
    write_json(p, tax)
    return tax
