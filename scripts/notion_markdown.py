"""Convert a Notion page and its blocks into a markdown record.

The converter is deliberately small and permissive. It targets the block
types the sync pipeline most commonly ingests and ignores unknown block
kinds rather than failing. Rich-text annotations are rendered as inline
markdown for bold, italic, inline code, and links. All other annotations
fall back to plain text.
"""

from __future__ import annotations

from typing import Any


# Block handlers render a single block and return an optional markdown
# string. List-style blocks (bulleted, numbered, to_do) render with their
# own prefix and are joined with a single newline so adjacent list items
# stay tight.


def _rich_text_to_markdown(runs: list[dict[str, Any]] | None) -> str:
    if not runs:
        return ""
    pieces: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        text = run.get("plain_text")
        if text is None:
            text_obj = run.get("text") or {}
            text = text_obj.get("content", "")
        text = str(text)
        annotations = run.get("annotations") or {}
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        href = run.get("href")
        if not href:
            link_obj = (run.get("text") or {}).get("link") or {}
            href = link_obj.get("url")
        if href:
            text = f"[{text}]({href})"
        pieces.append(text)
    return "".join(pieces)


def _block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type", "")
    payload = block.get(block_type) or {}
    return _rich_text_to_markdown(payload.get("rich_text"))


def _render_block(block: dict[str, Any]) -> str | None:
    block_type = block.get("type")
    if not block_type:
        return None
    payload = block.get(block_type) or {}

    if block_type == "heading_1":
        return f"# {_block_text(block)}".rstrip()
    if block_type == "heading_2":
        return f"## {_block_text(block)}".rstrip()
    if block_type == "heading_3":
        return f"### {_block_text(block)}".rstrip()
    if block_type == "paragraph":
        text = _block_text(block)
        return text if text else ""
    if block_type == "bulleted_list_item":
        return f"- {_block_text(block)}".rstrip()
    if block_type == "numbered_list_item":
        return f"1. {_block_text(block)}".rstrip()
    if block_type == "to_do":
        checked = bool(payload.get("checked"))
        marker = "[x]" if checked else "[ ]"
        return f"- {marker} {_block_text(block)}".rstrip()
    if block_type == "code":
        language = str(payload.get("language") or "").strip()
        body = _rich_text_to_markdown(payload.get("rich_text")) or ""
        fence_open = f"```{language}" if language else "```"
        return f"{fence_open}\n{body}\n```".rstrip()
    if block_type == "quote":
        text = _block_text(block)
        if not text:
            return "> "
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
    if block_type == "divider":
        return "---"
    if block_type == "child_page":
        title = str(payload.get("title") or "Untitled")
        child_id = str(block.get("id") or "").replace("-", "")
        if child_id:
            return f"- [{title}](notion://{child_id})"
        return f"- {title}"

    # Unknown block type — render nothing but do not crash.
    # TODO: extend support for: table, table_row, toggle, callout,
    #   image, bookmark, embed, file, equation, column, column_list,
    #   synced_block, template, breadcrumb, table_of_contents,
    #   link_preview, link_to_page.
    return None


_LIST_BLOCK_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}


def _join_blocks(rendered: list[tuple[str, str]]) -> str:
    """Join rendered blocks, keeping adjacent list items tight."""
    if not rendered:
        return ""
    chunks: list[str] = []
    prev_type: str | None = None
    for block_type, text in rendered:
        if not chunks:
            chunks.append(text)
        else:
            if (
                block_type in _LIST_BLOCK_TYPES
                and prev_type in _LIST_BLOCK_TYPES
                and block_type == prev_type
            ):
                chunks.append("\n")
            else:
                chunks.append("\n\n")
            chunks.append(text)
        prev_type = block_type
    return "".join(chunks).strip()


def _extract_title(page: dict[str, Any]) -> str:
    properties = page.get("properties") or {}
    if not isinstance(properties, dict):
        return ""
    for value in properties.values():
        if isinstance(value, dict) and value.get("type") == "title":
            runs = value.get("title") or []
            title = _rich_text_to_markdown(runs)
            if title:
                return title
    return ""


def _extract_parent(page: dict[str, Any]) -> dict[str, Any]:
    parent = page.get("parent") or {}
    if not isinstance(parent, dict):
        return {"type": "workspace", "id": None}
    parent_type = str(parent.get("type") or "workspace")
    parent_id = parent.get(parent_type)
    if parent_type == "workspace":
        return {"type": "workspace", "id": None}
    return {"type": parent_type, "id": parent_id}


def page_to_markdown(
    page: dict[str, Any], blocks: list[dict[str, Any]]
) -> tuple[str, str, dict[str, Any]]:
    """Convert a Notion page plus its blocks to (title, markdown, metadata)."""
    title = _extract_title(page)

    rendered: list[tuple[str, str]] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = _render_block(block)
        if text is None:
            continue
        rendered.append((str(block.get("type") or ""), text))
    markdown_body = _join_blocks(rendered)

    notion_page_id = str(page.get("id") or "").replace("-", "")
    metadata: dict[str, Any] = {
        "notion_page_id": notion_page_id,
        "last_edited_time": page.get("last_edited_time"),
        "created_time": page.get("created_time"),
        "parent": _extract_parent(page),
    }
    if page.get("url"):
        metadata["url"] = page["url"]
    if page.get("archived") is not None:
        metadata["archived"] = bool(page["archived"])

    return title, markdown_body, metadata
