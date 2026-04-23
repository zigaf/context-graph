from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_regions import extract_regions  # noqa: E402


class ExtractRegionsTests(unittest.TestCase):
    def test_passes_through_structured_content(self):
        record = {
            "title": "X",
            "content": "ignored",
            "structuredContent": {
                "frontmatter": "status: in-progress",
                "metadataBlock": "",
                "titleText": "X",
                "breadcrumb": "parent > child",
                "body": "pre-parsed body",
            },
        }
        regions = extract_regions(record)
        self.assertEqual(regions["body"], "pre-parsed body")
        self.assertEqual(regions["breadcrumb"], "parent > child")

    def test_parses_yaml_frontmatter(self):
        content = "---\nstatus: in-progress\ntype: task\n---\n\n# Heading\nbody text"
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("status: in-progress", regions["frontmatter"])
        self.assertIn("type: task", regions["frontmatter"])
        self.assertNotIn("---", regions["frontmatter"])

    def test_extracts_metadata_block(self):
        content = (
            "## Metadata\n"
            "- **status**: in-progress\n"
            "- **room**: core\n"
            "\n"
            "# Main\n"
            "body content"
        )
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("status", regions["metadataBlock"])
        self.assertIn("room", regions["metadataBlock"])
        self.assertNotIn("## Metadata", regions["metadataBlock"])
        self.assertIn("body content", regions["body"])
        self.assertNotIn("status", regions["body"])

    def test_pulls_breadcrumb_from_source_metadata(self):
        record = {
            "title": "T",
            "content": "body",
            "source": {"metadata": {"parent": "kenmore > Architecture"}},
        }
        regions = extract_regions(record)
        self.assertEqual(regions["breadcrumb"], "kenmore > Architecture")

    def test_all_regions_present_even_when_empty(self):
        record = {"title": "Lonely", "content": "just body"}
        regions = extract_regions(record)
        self.assertEqual(
            set(regions.keys()),
            {"frontmatter", "metadataBlock", "titleText", "breadcrumb", "body"},
        )
        self.assertEqual(regions["titleText"], "Lonely")
        self.assertEqual(regions["body"], "just body")

    def test_recognizes_localized_metadata_heading(self):
        content = "## Метадані\n- статус: у процесі\n\n# Основне\nтіло"
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("статус", regions["metadataBlock"])


if __name__ == "__main__":
    unittest.main()
