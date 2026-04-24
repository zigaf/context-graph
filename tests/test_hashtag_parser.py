from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hashtag_parser import parse_hashtags  # noqa: E402


class HashtagParserTests(unittest.TestCase):
    SCHEMA = {
        "markers": {
            "type": ["task", "bug", "rule", "decision"],
            "domain": ["payments", "auth", "api"],
            "scope": ["convention", "gotcha", "intersection"],
            "artifact": ["webhook", "endpoint"],
        }
    }

    def test_no_hashtags_returns_query_unchanged(self):
        q, m = parse_hashtags("how do we handle webhooks", self.SCHEMA)
        self.assertEqual(q, "how do we handle webhooks")
        self.assertEqual(m, {})

    def test_single_hashtag_resolves_to_axis(self):
        q, m = parse_hashtags("#rule payments retries", self.SCHEMA)
        self.assertEqual(q, "payments retries")
        self.assertEqual(m, {"type": "rule"})

    def test_multiple_hashtags_anded(self):
        q, m = parse_hashtags("#rule #payments", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {"type": "rule", "domain": "payments"})

    def test_mixed_query_and_hashtags(self):
        q, m = parse_hashtags("how do #api #webhook services talk", self.SCHEMA)
        self.assertEqual(q, "how do services talk")
        self.assertEqual(m, {"domain": "api", "artifact": "webhook"})

    def test_unknown_hashtag_kept_in_query(self):
        q, m = parse_hashtags("#mystery #rule x", self.SCHEMA)
        # Unknown tag stays as a regular word; only the known one is moved.
        self.assertEqual(q, "#mystery x")
        self.assertEqual(m, {"type": "rule"})

    def test_empty_query(self):
        q, m = parse_hashtags("", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {})

    def test_only_hashtags(self):
        q, m = parse_hashtags("#rule #convention #payments", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {"type": "rule", "scope": "convention", "domain": "payments"})

    def test_repeated_hashtag_last_wins(self):
        q, m = parse_hashtags("#rule #decision", self.SCHEMA)
        # Both target axis ``type``; keep the last one to give the user a
        # predictable override semantics.
        self.assertEqual(m["type"], "decision")

    def test_case_insensitive_match(self):
        q, m = parse_hashtags("#Rule #PAYMENTS", self.SCHEMA)
        self.assertEqual(m, {"type": "rule", "domain": "payments"})


if __name__ == "__main__":
    unittest.main()
