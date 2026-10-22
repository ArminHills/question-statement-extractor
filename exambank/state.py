# -*- coding: utf-8 -*-
"""state.json: منبع حقیقت برای resume (دیگر به پارس docx وابسته نیستیم)."""
import time
from pathlib import Path

from .utils import read_json, write_json


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.data = read_json(self.path, None) or {"version": 1, "stages": {}, "quota": None}
        self.data.setdefault("stages", {})
        self.data.setdefault("quota", None)

    def _stage(self, stage):
        return self.data["stages"].setdefault(stage, {})

    def save(self):
        write_json(self.path, self.data)

    def mark_ok(self, stage, key, **extra):
        self._stage(stage)[str(key)] = dict(status="ok", at=time.time(), **extra)
        self.save()

    def mark_failed(self, stage, key, error):
        self._stage(stage)[str(key)] = {"status": "failed", "error": str(error), "at": time.time()}
        self.save()

    def is_done(self, stage, key):
        return self._stage(stage).get(str(key), {}).get("status") == "ok"

    def failed(self, stage):
        return [k for k, v in self._stage(stage).items() if v.get("status") == "failed"]

    def set_quota_exhausted(self, stage, reason):
        self.data["quota"] = {"stage": stage, "reason": reason, "at": time.time()}
        self.save()

    def clear_quota(self):
        self.data["quota"] = None
        self.save()

    @property
    def quota(self):
        return self.data.get("quota")

    def reset_stage(self, stage):
        self.data["stages"][stage] = {}
        self.save()
