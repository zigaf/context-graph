from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_idf import (  # noqa: E402
    compute_idf_from_records,
    load_idf_stats,
    save_idf_stats,
)


class IdfComputationTests(unittest.TestCase):
    def test_empty_corpus_returns_empty(self):
        self.assertEqual(
            compute_idf_from_records([]),
            {"corpusSize": 0, "tokenDocumentFrequency": {}},
        )

    def test_counts_unique_tokens_per_document(self):
        records = [
            {"id": "a", "title": "apple banana", "content": "apple cherry"},
            {"id": "b", "title": "banana", "content": "banana"},
            {"id": "c", "title": "cherry", "content": "cherry"},
        ]
        idf = compute_idf_from_records(records)
        self.assertEqual(idf["corpusSize"], 3)
        token_document_frequency = idf["tokenDocumentFrequency"]
        self.assertEqual(token_document_frequency["apple"], 1)
        self.assertEqual(token_document_frequency["banana"], 2)
        self.assertEqual(token_document_frequency["cherry"], 2)

    def test_token_counted_once_per_document_even_if_repeated(self):
        records = [{"id": "x", "title": "word", "content": "word word word"}]
        idf = compute_idf_from_records(records)
        self.assertEqual(idf["tokenDocumentFrequency"]["word"], 1)


class IdfStorageTests(unittest.TestCase):
    def test_load_returns_uniform_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = load_idf_stats(Path(tmp) / "nope.json")
            self.assertEqual(stats["corpusSize"], 0)
            self.assertEqual(stats["tokenDocumentFrequency"], {})

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "idf.json"
            save_idf_stats(path, {"corpusSize": 3, "tokenDocumentFrequency": {"a": 2}})
            loaded = load_idf_stats(path)
            self.assertEqual(loaded["corpusSize"], 3)
            self.assertEqual(loaded["tokenDocumentFrequency"]["a"], 2)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], "1")
            self.assertIn("updatedAt", raw)


if __name__ == "__main__":
    unittest.main()
