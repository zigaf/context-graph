from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import context_graph_mcp  # noqa: E402


class MCPIntentSchemaTests(unittest.TestCase):
    def _tool(self, name: str):
        for t in context_graph_mcp.TOOLS:
            if t.name == name:
                return t
        self.fail(f"Tool {name} not registered")

    def test_build_context_pack_advertises_intentMode(self):
        tool = self._tool("build_context_pack")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        enum = props["intentMode"].get("enum")
        for name in ("debug", "implementation", "architecture", "product"):
            self.assertIn(name, enum)
        self.assertIn("intentOverride", props)

    def test_search_graph_advertises_intentMode(self):
        tool = self._tool("search_graph")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        self.assertIn("intentOverride", props)

    def test_inspect_record_advertises_intentMode(self):
        tool = self._tool("inspect_record")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        self.assertIn("intentOverride", props)


if __name__ == "__main__":
    unittest.main()
