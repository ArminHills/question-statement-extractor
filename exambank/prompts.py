# -*- coding: utf-8 -*-
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name):
    """prompts/<name>.fa.md. متن کامل وارد کلید کش می‌شود (ویرایش prompt = کش جدید)."""
    return (PROMPTS_DIR / f"{name}.fa.md").read_text(encoding="utf-8")
