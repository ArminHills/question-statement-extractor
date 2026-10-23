# -*- coding: utf-8 -*-
"""بررسی مدل‌های Pydantic. اگر pydantic نصب نباشد skip می‌شود.
توجه: در محیطی که این کد نوشته شد pydantic نبود، پس این فایل آن‌جا اجرا نشده است."""
import unittest

try:
    import pydantic  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

from exambank.schemas import build_pydantic_models, validate_page_payload
from tests.test_extract_stage import PAGES


@unittest.skipUnless(HAVE, "pydantic نصب نیست")
class PydanticModels(unittest.TestCase):
    def test_fixtures_accepted_by_model_and_validator(self):
        Model = build_pydantic_models()
        for name, payload in PAGES.items():
            Model.model_validate(payload)
            validate_page_payload(payload)

    def test_json_schema_exports(self):
        schema = build_pydantic_models().model_json_schema()
        self.assertIn("properties", schema)
        self.assertEqual(set(schema["required"]), {"page_role", "questions", "answers", "figures"})

    def test_model_rejects_bad_role(self):
        Model = build_pydantic_models()
        with self.assertRaises(Exception):
            Model.model_validate({"page_role": "zzz", "questions": [], "answers": [], "figures": []})


if __name__ == "__main__":
    unittest.main()
