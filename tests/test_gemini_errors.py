import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import conf
from gemini import get_user_error_message
from google.genai import errors


class GeminiErrorMessageTest(unittest.TestCase):
    def test_quota_error_is_sanitized(self) -> None:
        error = errors.ClientError(
            429,
            {
                "error": {
                    "message": "quota json with internal details",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )

        message = get_user_error_message(error)

        self.assertEqual(message, conf["quota_error_info"])
        self.assertNotIn("quota json with internal details", message)
        self.assertNotIn("RESOURCE_EXHAUSTED", message)

    def test_generic_error_uses_default_message(self) -> None:
        self.assertEqual(get_user_error_message(RuntimeError("boom")), conf["error_info"])


if __name__ == "__main__":
    unittest.main()
