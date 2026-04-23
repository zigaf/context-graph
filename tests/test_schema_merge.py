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

from classifier_schema import load_merged_schema  # noqa: E402


class SchemaMergeTests(unittest.TestCase):
    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_shipped_only_when_no_overlays(self):
        schema = load_merged_schema(overlay_path=None, learned_path=None)
        self.assertIn("domain", schema["markers"])
        self.assertIn("payments", schema["markers"]["domain"])

    def test_learned_accepted_unions_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "learned.json"
            self._write(learned, {"accepted": {"domain": ["challenge", "promo"]}})
            schema = load_merged_schema(overlay_path=None, learned_path=learned)
            self.assertIn("challenge", schema["markers"]["domain"])
            self.assertIn("promo", schema["markers"]["domain"])
            self.assertIn("payments", schema["markers"]["domain"])

    def test_overlay_union_and_alias_concat(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.json"
            self._write(
                overlay,
                {
                    "markers": {"domain": ["ib-commission"]},
                    "aliases": {"domain": {"challenge": ["challenge-account"]}},
                },
            )
            learned = Path(tmp) / "learned.json"
            self._write(learned, {"accepted": {"domain": ["challenge"]}})
            schema = load_merged_schema(overlay_path=overlay, learned_path=learned)
            self.assertIn("ib-commission", schema["markers"]["domain"])
            self.assertIn("challenge", schema["markers"]["domain"])
            self.assertIn("challenge-account", schema["aliases"]["domain"]["challenge"])

    def test_rejected_values_are_not_in_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "learned.json"
            self._write(
                learned,
                {
                    "accepted": {"domain": ["challenge"]},
                    "proposals": {"rejected": [{"value": "bl-api", "field": "domain"}]},
                },
            )
            schema = load_merged_schema(overlay_path=None, learned_path=learned)
            self.assertNotIn("bl-api", schema["markers"]["domain"])

    def test_new_field_in_overlay_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.json"
            self._write(overlay, {"markers": {"room": ["core", "il", "pat"]}})
            schema = load_merged_schema(overlay_path=overlay, learned_path=None)
            self.assertIn("room", schema["markers"])
            self.assertIn("core", schema["markers"]["room"])


if __name__ == "__main__":
    unittest.main()
