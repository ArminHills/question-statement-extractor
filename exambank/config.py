# -*- coding: utf-8 -*-
from pathlib import Path

DEFAULTS = {
    "models": {"extract": "gemini-3.5-flash-lite", "classify": "gemini-3.5-flash-lite",
               "atomize": "gemini-3.5-flash-lite"},
    "request_delay_seconds": 3, "batch_size": 1, "similarity_threshold": 90,
    "expected_option_counts": [4], "max_consecutive_rate_limits": 2, "render_scale": 2.0,
}


def load_config(path=None):
    """config.yaml کنار پروژه؛ کلیدهای غایب از DEFAULTS پر می‌شوند."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = Path(path) if path else Path(__file__).resolve().parent.parent / "config.yaml"
    if p.exists():
        import yaml
        with open(p, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
