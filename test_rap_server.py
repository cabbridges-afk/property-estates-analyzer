import json
import unittest
from unittest.mock import patch

import rap_server


class RapServerTests(unittest.TestCase):
    def test_parse_json_object_accepts_code_fence(self):
        self.assertEqual(
            rap_server.parse_json_object("```json\n{\"conclusion\":\"likely valid\"}\n```"),
            {"conclusion": "likely valid"},
        )

    def test_normalize_cases_filters_blocked_and_normalizes_urls(self):
        cases = rap_server.normalize_cases({"results": [
            {"blocked": True, "caseName": "Hidden"},
            {
                "caseName": "Estate of Test",
                "citation": ["1 Test 2"],
                "court": "Example Court",
                "dateFiled": "2001-01-01",
                "absolute_url": "/opinion/1/estate-of-test/",
                "opinions": [{"snippet": "<mark>Rule</mark> against perpetuities"}],
            },
        ]})
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["url"], "https://www.courtlistener.com/opinion/1/estate-of-test/")
        self.assertEqual(cases[0]["snippet"], "Rule against perpetuities")

    @patch.dict("os.environ", {}, clear=True)
    @patch("rap_server.get_json", return_value={"results": []})
    def test_missing_openrouter_key_preserves_case_research(self, _search):
        result = rap_server.analyze_rap({
            "hypothetical": "O to A for life, then to B if B reaches 21.",
            "classification": [{"party": "B", "label": "Contingent Remainder in Fee Simple"}],
        })
        self.assertIsNone(result["memo"])
        self.assertIn("not configured", result["ai_error"])
        self.assertEqual(result["cases"], [])

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True)
    @patch("rap_server.get_json", return_value={"results": []})
    @patch("rap_server.json_request")
    def test_ai_result_is_normalized(self, request, _search):
        request.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "conclusion": "likely void",
                "summary": "The interest might vest too remotely.",
                "interests": [{"interest": "C's executory interest", "rap_status": "void", "reason": "No validating life."}],
                "assumptions": ["Common-law RAP applies."],
                "study_note": "Check the jurisdiction.",
            })}}]
        }
        result = rap_server.analyze_rap({"hypothetical": "O to A, then to C if.", "classification": []})
        self.assertEqual(result["memo"]["conclusion"], "likely void")
        self.assertEqual(result["memo"]["interests"][0]["rap_status"], "void")
        self.assertIsNone(result["ai_error"])


if __name__ == "__main__":
    unittest.main()
