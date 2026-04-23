"""Convert a Notion page and its blocks into a markdown record.

The converter is deliberately small and permissive. It targets the block
types the sync pipeline most commonly ingests and ignores unknown block
kinds rather than failing. Rich-text annotations are rendered as inline
markdown for bold, italic, inline code, and links. All other annotations
fall back to plain text.

Nested block children (``table_row`` under ``table``, the body of a
``toggle`` / ``callout``, the blocks inside a ``column`` / ``column_list``)
are expected to be pre-fetched and attached to their parent block under
the key ``_children``. The collector in ``scripts/notion_sync.py`` is
responsible for populating that key via the Notion REST API; the
renderer here treats ``_children`` as an optional list of sibling blocks
and simply renders them recursively.
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


def _children(block: dict[str, Any]) -> list[dict[str, Any]]:
    raw = block.get("_children")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _render_children(block: dict[str, Any]) -> str:
    rendered: list[tuple[str, str]] = []
    for child in _children(block):
        text = _render_block(child)
        if text is None:
            continue
        rendered.append((str(child.get("type") or ""), text))
    return _join_blocks(rendered)


def _escape_cell(text: str) -> str:
    # Pipes inside GFM cells must be escaped. Newlines would break the
    # single-line cell contract, so collapse them too.
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _render_table(block: dict[str, Any]) -> str | None:
    payload = block.get("table") or {}
    rows = [
        child for child in _children(block) if child.get("type") == "table_row"
    ]
    if not rows:
        return ""
    width = int(payload.get("table_width") or 0) or max(
        len((row.get("table_row") or {}).get("cells") or []) for row in rows
    )
    if width <= 0:
        return ""

    def _row_cells(row: dict[str, Any]) -> list[str]:
        cells = (row.get("table_row") or {}).get("cells") or []
        rendered: list[str] = []
        for idx in range(width):
            runs = cells[idx] if idx < len(cells) else []
            rendered.append(_escape_cell(_rich_text_to_markdown(runs)))
        return rendered

    def _format_row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    lines: list[str] = []
    has_column_header = bool(payload.get("has_column_header"))
    if has_column_header:
        header_cells = _row_cells(rows[0])
        body_rows = rows[1:]
    else:
        header_cells = [""] * width
        body_rows = rows
    lines.append(_format_row(header_cells))
    lines.append(_format_row(["---"] * width))
    for row in body_rows:
        lines.append(_format_row(_row_cells(row)))
    return "\n".join(lines)


def _render_toggle(block: dict[str, Any]) -> str:
    summary = _block_text(block)
    body = _render_children(block)
    if body:
        return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"
    return f"<details><summary>{summary}</summary>\n\n</details>"


def _render_callout(block: dict[str, Any]) -> str:
    payload = block.get("callout") or {}
    text = _rich_text_to_markdown(payload.get("rich_text"))
    icon = payload.get("icon") or {}
    emoji = icon.get("emoji") if isinstance(icon, dict) else None
    head = f"{emoji} {text}".strip() if emoji else text
    header_line = f"> {head}" if head else ">"
    body = _render_children(block)
    if not body:
        return header_line
    continuation: list[str] = [header_line]
    for line in body.splitlines():
        continuation.append(f"> {line}" if line else ">")
    return "\n".join(continuation)


def _render_column_container(block: dict[str, Any]) -> str:
    # ``column_list`` and ``column`` are layout wrappers — flatten their
    # children inline. Child columns (inside a column_list) are separated
    # by a blank line so each column's content stays a distinct paragraph.
    return _render_children(block)


def _render_link_to_page(block: dict[str, Any]) -> str | None:
    payload = block.get("link_to_page") or {}
    link_type = str(payload.get("type") or "").strip()
    target_id: str | None = None
    if link_type:
        target_id = payload.get(link_type)
    if not target_id:
        return None
    normalized = str(target_id).replace("-", "")
    if not normalized:
        return None
    return f"[Untitled](notion://{normalized})"


def _render_image(block: dict[str, Any]) -> str | None:
    payload = block.get("image") or {}
    image_type = str(payload.get("type") or "").strip()
    url: str | None = None
    if image_type:
        holder = payload.get(image_type) or {}
        if isinstance(holder, dict):
            url = holder.get("url")
    if not url:
        # Fall back to whichever holder carries a url so we tolerate
        # payloads with an unexpected ``type`` value.
        for key in ("file", "external"):
            holder = payload.get(key) or {}
            if isinstance(holder, dict) and holder.get("url"):
                url = holder["url"]
                break
    if not url:
        return None
    alt = _rich_text_to_markdown(payload.get("caption"))
    return f"![{alt}]({url})"


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
    if block_type == "table":
        return _render_table(block)
    if block_type == "toggle":
        return _render_toggle(block)
    if block_type == "callout":
        return _render_callout(block)
    if block_type in {"column_list", "column"}:
        return _render_column_container(block)
    if block_type == "link_to_page":
        return _render_link_to_page(block)
    if block_type == "image":
        return _render_image(block)

    # Unknown block type — render nothing but do not crash.
    # TODO: extend support for: bookmark, embed, file, equation,
    #   synced_block, template, breadcrumb, table_of_contents,
    #   link_preview.
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
