"""Light project sniff for the curator bootstrap flow.

Reads README + manifests + top-level dir tree (depth 2, capped) to
produce a preview dict. No I/O to Notion happens here — that lives in
the slash command and the MCP orchestration. This module is pure
stdlib and side-effect free except for the file reads listed above.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_NOISE_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", "out", "target",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode",
    ".next", ".nuxt",
}

_README_LINE_LIMIT = 200
_DIR_LIMIT = 30


def _read_readme(root: Path) -> tuple[str, str]:
    """Return ``(title, tagline)`` from the project README, or ``("", "")``
    when none is found."""
    for name in ("README.md", "README.MD", "Readme.md", "readme.md"):
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()[:_README_LINE_LIMIT]
            title = ""
            tagline = ""
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not title and stripped.startswith("# "):
                    title = stripped[2:].strip()
                    # First non-empty non-heading paragraph is the tagline:
                    for next_line in lines[i + 1:]:
                        nxt = next_line.strip()
                        if not nxt:
                            continue
                        if nxt.startswith("#"):
                            break
                        tagline = nxt
                        break
                    break
            return title, tagline
    return "", ""


def _read_manifest(root: Path) -> tuple[str, str]:
    """Return ``(title, tagline)`` from a manifest if present.

    Tries ``package.json``, ``pyproject.toml``, ``Cargo.toml``, ``go.mod``
    in that order. Returns ``("", "")`` when nothing is found.
    """
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            return str(data.get("name") or ""), str(data.get("description") or "")
    py = root / "pyproject.toml"
    if py.exists():
        # Stdlib has tomllib in 3.11+. Use it lazily to keep the import
        # local — older interpreters in tests can still load this module.
        try:
            import tomllib
            data = tomllib.loads(py.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            project = data.get("project") or data.get("tool", {}).get("poetry") or {}
            if isinstance(project, dict):
                return str(project.get("name") or ""), str(project.get("description") or "")
    cargo = root / "Cargo.toml"
    if cargo.exists():
        # Cheap parse: scan for `name = "..."` and `description = "..."`
        # under [package]. Avoids a tomllib dependency if running on 3.10.
        text = cargo.read_text(encoding="utf-8", errors="replace")
        in_package = False
        title = ""
        tagline = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                in_package = (s == "[package]")
                continue
            if in_package and s.startswith("name = "):
                title = s.split("=", 1)[1].strip().strip('"')
            elif in_package and s.startswith("description = "):
                tagline = s.split("=", 1)[1].strip().strip('"')
        if title:
            return title, tagline
    return "", ""


def _list_top_level_dirs(root: Path) -> list[dict[str, str]]:
    """Return up to _DIR_LIMIT top-level dirs as ``{path, purpose}`` dicts.

    ``purpose`` is left empty in this layer — the curator skill / LLM
    fills it during the session-time orchestration. We only seed the
    structural part here.
    """
    if not root.is_dir():
        return []
    entries = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in _NOISE_DIRS or name.startswith("."):
            continue
        entries.append({"path": f"{name}/", "purpose": ""})
        if len(entries) >= _DIR_LIMIT:
            break
    return entries


def bootstrap_project_skeleton(workspace_root: Path | str) -> dict[str, Any]:
    """Sniff project + return a preview dict for the bootstrap flow."""
    root = Path(str(workspace_root)).resolve()
    readme_title, readme_tagline = _read_readme(root)
    manifest_title, manifest_tagline = _read_manifest(root)
    title = readme_title or manifest_title or root.name
    tagline = readme_tagline or manifest_tagline or ""
    dirs = _list_top_level_dirs(root)
    return {
        "projectTitle": title,
        "tagline": tagline,
        "topLevelDirs": dirs,
        "rootPath": str(root),
    }
