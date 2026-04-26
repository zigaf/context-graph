"""Bootstrap content generator for Context Graph Notion pages.

Pure functions: read project files, return strings. No network calls,
no LLM calls. Designed to be cheap so /cg-bootstrap can call it for
every dir without burning tokens.

Source materials per dir, in order:

- ``<dir>/README.md`` first paragraph (if present)
- ``<dir>/package.json#dependencies`` (or Cargo.toml / pyproject.toml /
  requirements.txt) for the Stack line
- A directory listing capped at five files for the Entry points line
- A heuristic fallback derived from the dir name when nothing matches
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


_HEURISTIC_FALLBACKS: dict[str, str] = {
    "api": "API service.",
    "admin": "Admin UI.",
    "core": "Core domain logic.",
    "db": "Database layer.",
    "docs": "Project documentation.",
    "scripts": "Operational and dev scripts.",
    "skills": "Plugin skill definitions.",
    "tests": "Test suite.",
    "commands": "Slash command definitions.",
}


def _first_paragraph(text: str) -> str:
    paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
    if not paragraphs:
        return ""
    first = paragraphs[0]
    if first.startswith("#"):
        # Skip the leading heading; use the next paragraph if any.
        if len(paragraphs) > 1:
            return paragraphs[1]
        return ""
    return first


def _read_dependencies(dir_path: Path) -> list[str]:
    pkg = dir_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        deps = data.get("dependencies") if isinstance(data, dict) else {}
        if isinstance(deps, dict):
            return list(deps.keys())[:5]
    pyproject = dir_path / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # Cheap parse: look for a dependencies = [...] block.
        if "dependencies" in text:
            return ["python (pyproject)"]
    requirements = dir_path / "requirements.txt"
    if requirements.exists():
        try:
            lines = [
                line.strip().split("=")[0].split("<")[0].split(">")[0]
                for line in requirements.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except OSError:
            lines = []
        return lines[:5]
    return []


def _list_entry_files(dir_path: Path, *, max_files: int = 5) -> list[str]:
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    candidates = sorted(
        item.name for item in dir_path.iterdir()
        if item.is_file() and not item.name.startswith(".")
    )
    return candidates[:max_files]


def _heuristic_purpose(dir_name: str) -> str:
    plain = dir_name.rstrip("/")
    for keyword, label in _HEURISTIC_FALLBACKS.items():
        if keyword in plain.lower():
            return label
    return f"Module: {plain}/."


def build_dir_paragraph(dir_path: Path) -> str:
    """Return the auto-generated paragraph body for a top-level dir page."""
    name = dir_path.name + "/"
    readme = dir_path / "README.md"
    notes = ""
    if readme.exists():
        try:
            notes = _first_paragraph(readme.read_text(encoding="utf-8"))
        except OSError:
            notes = ""
    purpose = notes if notes else _heuristic_purpose(dir_path.name)
    deps = _read_dependencies(dir_path)
    files = _list_entry_files(dir_path)
    parts: list[str] = [f"Purpose: {purpose}"]
    if deps:
        parts.append("Stack: " + ", ".join(deps))
    if files:
        parts.append("Entry points: " + ", ".join(files))
    if notes and purpose != notes:
        parts.append(f"Notes: {notes}")
    parts.insert(0, name)
    return "\n".join(parts)


def build_root_body(
    repo: Path,
    *,
    project_title: str,
    top_level_dirs: Iterable[dict],
) -> str:
    """Return the Notion body for the root project page."""
    readme = repo / "README.md"
    tagline = ""
    if readme.exists():
        try:
            tagline = _first_paragraph(readme.read_text(encoding="utf-8"))
        except OSError:
            tagline = ""
    lines: list[str] = [f"# {project_title}", ""]
    if tagline:
        lines.extend([tagline, ""])
    lines.extend([
        "## Directories",
        "",
    ])
    for dir_entry in top_level_dirs:
        path = dir_entry.get("path") or ""
        if path:
            lines.append(f"- {path}")
    lines.extend([
        "",
        "## Indexes",
        "",
        "Index pages (Rules, Decisions, Gotchas, Module Boundaries, "
        "Conventions, Tasks, Bug Fixes) are created on the first auto-push "
        "of a record of that type.",
        "",
        "_Maintained by Context Graph plugin. Auto-sections are rewritten on "
        "/cg-bootstrap --refresh._",
    ])
    return "\n".join(lines)
