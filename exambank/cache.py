# -*- coding: utf-8 -*-
"""کش روی دیسک برای فراخوانی‌های LLM.
کلید = sha256(ورودی + متن prompt + PROMPT_VERSION + مدل + SCHEMA_VERSION).
فقط پاسخ‌های موفق و معتبر ذخیره می‌شوند؛ شکست هرگز کش نمی‌شود."""
import hashlib
import json
from pathlib import Path

from .utils import atomic_write_text


def digest_parts(parts):
    """parts: لیست (نوع, محتوا) با نوع 'text' یا 'pdf'/'bytes'."""
    h = hashlib.sha256()
    for kind, content in parts:
        h.update(kind.encode("utf-8") + b"\0")
        h.update(content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8"))
        h.update(b"\1")
    return h.hexdigest()


def make_key(task, system_prompt, parts, prompt_version, model, schema_version):
    payload = json.dumps(
        [task, hashlib.sha256((system_prompt or "").encode("utf-8")).hexdigest(),
         digest_parts(parts), prompt_version, model, schema_version],
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, root):
        self.root = Path(root)
        self.hits = 0
        self.misses = 0

    def _path(self, key):
        return self.root / key[:2] / f"{key}.json"

    def get(self, key):
        p = self._path(key)
        try:
            with open(p, "r", encoding="utf-8") as f:
                value = json.load(f)["value"]
        except (OSError, ValueError, KeyError):
            self.misses += 1
            return None  # فایل خراب = نبودن
        self.hits += 1
        return value

    def put(self, key, value):
        atomic_write_text(self._path(key), json.dumps({"value": value}, ensure_ascii=False))
