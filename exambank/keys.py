# -*- coding: utf-8 -*-
"""مدیریت کلید API (همان رفتار نسخه‌ی اصلی: .env کنار برنامه، یا پرسش مخفی در CLI)."""
import os
import sys
from getpass import getpass
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def get_api_key_cli():
    from dotenv import load_dotenv, set_key
    load_dotenv(ENV_PATH)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    if not sys.stdin.isatty():
        return None
    key = getpass("Gemini API key (input hidden): ").strip()
    if not key:
        return None
    if input("Save it to the .env file next to the script? [y/N]: ").strip().lower() == "y":
        try:
            if not ENV_PATH.exists():
                ENV_PATH.touch()
            set_key(str(ENV_PATH), "GEMINI_API_KEY", key)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Could not save the key to .env: {e}")
    os.environ["GEMINI_API_KEY"] = key
    return key
