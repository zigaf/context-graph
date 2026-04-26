# Context Graph

Plugin for Claude Code (and Codex) that turns scattered project notes — Notion pages, markdown files, in-session captures — into a structured retrieval system.

Instead of free-form hashtags, every note is normalized into:

- **markers** — `type`, `domain`, `flow`, `goal`, `status` from a controlled vocabulary
- **hierarchy** — a stable path like `payments > deposit > endpoint`
- **explicit relations** — confirmed links such as `fixes`, `affects`, `depends_on`
- **inferred relations** — probable links with a confidence score

You ask for context, the plugin returns a compact pack ranked for the task — no scrolling through 200 Notion pages.

---

## Install

In Claude Code:

```text
/plugin marketplace add zigaf/context-graph
/plugin install context-graph@context-graph-marketplace
```

Restart the session so the MCP server and slash commands load. Verify with `/cg-start` — if the command is visible, the plugin is live.

The Notion side uses the official Notion MCP OAuth connection. **No API key is stored in the plugin.** Connect Notion via your usual session OAuth before the first sync.

---

## Five-minute first run

From any project directory:

```text
/cg-start
```

The wizard:

1. Creates a workspace at `<cwd>/.context-graph/`
2. Asks whether the first source is **Notion** or **local markdown**
3. Pulls existing notes from your chosen source
4. Indexes them locally
5. Suggests a `/cg-search` query so you can verify

That's the whole onboarding. The graph is now usable in this directory.

---

## Notion vs local markdown

|                | Notion                                              | Local markdown                            |
| -------------- | --------------------------------------------------- | ----------------------------------------- |
| Source         | pages in your Notion workspace                      | `.md` files under a folder                |
| Auth           | official Notion MCP OAuth (no key in plugin config) | none — reads files directly               |
| Use when       | team-shared knowledge already in Notion             | personal notes, scratch repos, MD exports |
| Updates        | per-page `last_edited_time` cursor                  | per-file freshness cursor                 |

Both produce the same local graph. You can run them in the same workspace.

---

## How notes get to Notion (auto-push)

Once `/cg-bootstrap` runs and the Notion side is connected:

- **You write notes locally.** Curator captures rules, decisions,
  gotchas, module boundaries, conventions, tasks, and bug fixes during
  your session and stores them in the local graph.
- **Captures queue up.** They land in `.context-graph/notion_push.json`
  under `pending`.
- **A trigger fires.** Any of these counts as a logical session-end:
  - a phrase like `готово`, `ship it`, `merged`, `done`, `закоммитим`
  - a `git commit`, `git push`, `git merge`, `git tag` command
  - completion of `/commit`, `/create-pr`, `/ship`, `/pr-review`
- **The plugin pushes silently.** Pending records are batch-pushed to
  Notion under their matching dir page. A summary block prints in chat
  with the new page names and locations.

To opt out, set `workspace.json.autoPush.enabled = false` and the
trigger script becomes a no-op.

`/cg-bootstrap` itself populates the root and per-dir pages with a
generated paragraph — no more empty stubs. Re-run with
`/cg-bootstrap --refresh` after major repo changes.

---

## Slash commands

| Command              | Purpose                                                                            |
| -------------------- | ---------------------------------------------------------------------------------- |
| `/cg-start`          | Onboarding wizard — init workspace + first sync (Notion or markdown).              |
| `/cg-init`           | Create `.context-graph/workspace.json` only, no sync.                              |
| `/cg-bootstrap`      | Create the Notion skeleton AND fill each page with a generated paragraph. Re-run with `--refresh` to regenerate. |
| `/cg-sync-notion`    | Pull Notion pages into the graph. The plugin's auto-push path uses an `auto` mode internally; you do not run it manually. |
| `/cg-index`          | Ingest a markdown folder into the graph.                                           |
| `/cg-search`         | Build a context pack for a task or query. Supports `#hashtag` marker filters.     |
| `/cg-classify`       | Normalize markers and hierarchy for a single note.                                 |
| `/cg-schema-learn`   | Run the schema learner — propose new marker values + update marker importance.    |
| `/cg-schema-review`  | Triage pending schema proposals (accept / reject / skip).                          |

Schema review is intentionally user-driven. Proposals are never auto-accepted.

---

## Workspace layout

After `/cg-init` (or `/cg-start`), the project gets:

```text
.context-graph/
  workspace.json          # opt-in marker, holds workspace id and Notion root mapping
  graph.json              # indexed records and edges (full bodies — keep out of git)
  schema.learned.json     # workspace-specific marker importance learned from your notes
  schema.feedback.json    # accept/reject decisions on schema proposals
  idf_stats.json          # retrieval ranking stats
  notion_cursor.json      # per-page last-seen timestamp for incremental Notion sync
  markdown_cursor.json    # per-file freshness state for incremental markdown sync
  notion_push.json        # local→Notion mapping for push idempotency
```

`workspace.json` is the **only** file checked in by default. Runtime state files are added to `.gitignore`. The shipped vocabulary lives in [docs/schema.json](docs/schema.json); workspace-specific learned values go to `.context-graph/schema.learned.json`.

---

## Record model

Every record carries:

- `id` — `notion:<page-id>` or `markdown:<file-hash>`
- `title`, `content`
- `markers` — `{type, domain, flow, goal, status, severity, scope, ...}` from the schema
- `hierarchy` — derived from markers, e.g. `payments > deposit > endpoint`
- `source` — `system`, `url`, `notionPageId`, `last_edited_time`, `parent` breadcrumb
- `relations` — explicit + inferred edges to other records

Full schema and adapter contract: [docs/record-model.md](docs/record-model.md), [docs/adapter-guide.md](docs/adapter-guide.md).

---

## Retrieval intent modes

`/cg-search` and `build_context_pack` accept an `intentMode` to bias ranking:

- `debug` — favour bugs, incidents, debug logs
- `implementation` — favour rules, conventions, related code paths
- `architecture` — favour decisions, architecture docs, module maps
- `product` — favour specs, user-facing changes

Override at query time with `--mode <name>` or `intentMode` in the payload. See [docs/retrieval.md](docs/retrieval.md).

---

## Curator workflow

The plugin ships a proactive curator skill (`context-graph-curator`) that watches sessions for high-signal artefacts — **rules, gotchas, decisions, module boundaries, conventions, tasks, bug fixes** — and proposes them as records. Hashtag UX in `/cg-search #rule #payments` filters the graph by markers without writing SQL.

See `docs/superpowers/specs/2026-04-24-proactive-curator-design.md` for the design.

---

## Promotion quality

`promote-pattern` derives a reusable rule from a cluster of related records. Output includes a `quality` block with:

- `score` and `recommendation` (`safe`, `review`, `split`)
- per-marker conflict counts
- `splitSuggestions` grouping source records by high-signal conflicts (`type`, `goal`, `artifact`)

Use this to decide whether a promoted rule ships as-is or splits into narrower records.

---

## CLI / MCP server

The plugin ships:

- **CLI** — `scripts/context_graph_cli.py`. JSON in / JSON out. Resolves the nearest `.context-graph/workspace.json` by walking up from cwd.
- **MCP server** — `scripts/context_graph_mcp.py`, registered in [.mcp.json](.mcp.json), exposes the CLI as MCP tools so Claude can call them directly.

Available CLI subcommands:

```text
classify-record       index-records         delete-record
init-workspace        search-graph          archive-record
link-record           promote-pattern       unarchive-record
build-context-pack    ingest-markdown
                      ingest-notion-export
                      sync-notion          # headless fallback for cron/CI
                      learn-schema
                      list-proposals
                      apply-proposal-decision
                      push-notion          # headless push fallback (--dry-run by default)
                      eval                 # retrieval regression check
```

In live sessions prefer the slash commands or MCP tools. The bare CLI is for cron, CI, and headless automation.

Examples:

```bash
echo '{"record":{"title":"Webhook race in deposit flow","content":"Duplicate payment creation after callback retry"}}' \
  | python3 scripts/context_graph_cli.py classify-record

echo '{}' | python3 scripts/context_graph_cli.py init-workspace

echo '{"rootPath":"/tmp/notes","graphPath":"/tmp/notes/graph.json"}' \
  | python3 scripts/context_graph_cli.py ingest-markdown
```

The Python `sync-notion` and `push-notion` commands are the headless fallback. They take `NOTION_TOKEN` from env and are only intended for cron/CI — live sessions go through the official Notion MCP via `/cg-sync-notion`.

---

## Tests

```bash
cd /path/to/context-graph
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
```

Fixture-based coverage in [tests/](tests/).

The retrieval eval harness (`scripts/context_graph_cli.py eval`) exits non-zero on regression against the committed baseline.

---

## Security and data

- [docs/security.md](docs/security.md) — headless Notion token handling. Live sessions use the Notion MCP OAuth; no API keys are stored.
- [docs/data-retention.md](docs/data-retention.md) — what `data/` and `.context-graph/` contain (`graph.json` carries full record bodies; `notion_cursor.json` is just a timestamp).
- [docs/lifecycle.md](docs/lifecycle.md) — record create / update / archive / delete, edge TTL, the optional redaction hook applied before a context pack is returned.

---

## License

MIT — see [LICENSE](LICENSE).
