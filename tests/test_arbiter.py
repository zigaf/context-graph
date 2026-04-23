from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import classify_record  # noqa: E402


class ClassifyRecordV2ShapeTests(unittest.TestCase):
    def test_deterministic_path_produces_classifier_notes(self):
        record = {
            "title": "Payments webhook incident",
            "content": (
                "## Metadata\n"
                "- **type**: bug\n"
                "- **domain**: payments\n"
                "- **status**: in-progress\n"
                "- **flow**: webhook\n"
                "\n"
                "# Detail\n"
                "Duplicate payment creation after callback retry."
            ),
            "source": {"metadata": {"parent": "kenmore > Architecture"}},
        }
        result = classify_record({"record": record})
        self.assertIn("markers", result)
        self.assertIn("hierarchy", result)

        notes = result["source"]["metadata"]["classifierNotes"]
        self.assertEqual(notes["classifierVersion"], "2")
        self.assertIn(notes["arbiter"], ("deterministic", "pending-arbitration", "fallback"))
        self.assertIn("scores", notes)
        self.assertIn("regionsUsed", notes)

    def test_pending_arbitration_emits_arbitration_request(self):
        record = {
            "title": "Deposit review",
            "content": "Notes about withdrawal deposit flows. Some payments logic.",
        }
        result = classify_record({"record": record})
        notes = result["source"]["metadata"]["classifierNotes"]
        if notes["arbiter"] == "pending-arbitration":
            self.assertIn("arbitrationRequest", result)
            request = result["arbitrationRequest"]
            self.assertIn("candidates", request)
            self.assertIn("allowedValues", request)
            self.assertIn("requiredFields", request)
            self.assertIn("instructions", request)

    def test_preserves_required_fields_list(self):
        result = classify_record({"record": {"title": "", "content": ""}})
        self.assertIn("missingRequiredMarkers", result)


if __name__ == "__main__":
    unittest.main()
