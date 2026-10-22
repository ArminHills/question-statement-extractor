# -*- coding: utf-8 -*-
import json
import os
import tempfile
from pathlib import Path


def parse_pages_input(page_input_str):
    """پارس امن یک رشته صفحات ناپیوسته مثل '250-255, 260, 270-275' به لیست مرتب.
    (همان رفتار نسخه‌ی اصلی مخزن)"""
    pages = []
    for part in page_input_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start_s, end_s = part.split('-')
            start, end = int(start_s.strip()), int(end_s.strip())
            if start > end:
                start, end = end, start
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    if not pages:
        raise ValueError("هیچ صفحه‌ای در ورودی پیدا نشد")
    return sorted(set(pages))


def is_valid_name_component(name):
    forbidden = set('<>:"/\\|?*')
    return len(name) > 0 and not any(ch in forbidden for ch in name)


def atomic_write_text(path, text):
    """نوشتن امن: اول فایل موقت، بعد جایگزینی. نیمه‌نوشته شدن فایل را غیرممکن می‌کند."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_jsonl(path, rows):
    atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
