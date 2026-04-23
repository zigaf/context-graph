# Security

Context Graph talks to Notion with an internal integration token. Everything
sensitive in the plugin flows through that token, so this page is scoped to how
to obtain it, pass it, and keep it out of logs and commits.

For on-disk graph data (which may contain the body of your notes), see
[data-retention.md](data-retention.md). For the lifecycle operations that
interact with token-protected flows (sync, archive, delete), see
[lifecycle.md](lifecycle.md).

## Obtaining a token

1. Go to <https://www.notion.so/my-integrations>.
2. Create a new internal integration. Give it a descriptive name (for example,
   `context-graph-local`).
3. Copy the secret token. It is shown once.
4. Share the specific databases and pages you want the plugin to read with
   that integration from inside Notion. **Do not** grant it workspace-wide
   access unless you genuinely need it.

## Passing the token to the plugin

Two inputs are supported. Both feed `sync_notion` and the underlying
`NotionClient`:

- **`NOTION_TOKEN` environment variable (preferred).** Export it in your shell
  profile, a `.env` file loaded outside the repo, or a secret manager. The
  plugin reads it at call time; nothing is cached on disk.
- **`token` field in the tool payload (fallback).** Useful for CI jobs or for
  one-off runs where you want to pin an explicit token without leaking it into
  the environment of unrelated processes. The payload value wins over the env
  var when both are present.

If neither is set, `sync_notion` returns an error and makes no API calls.

## Do not commit tokens

- Keep `.env` files out of git. The repo's `.gitignore` does not currently list
  them — add a line like `.env` and `.env.*` if you introduce one.
- Prefer a secret manager over plain files for anything you run more than once:
  macOS Keychain, [`pass`](https://www.passwordstore.org/), the 1Password CLI
  (`op read`), or Docker secrets for containerized runs.
- In CI, use the provider's encrypted secrets feature (for example,
  `secrets.NOTION_TOKEN` in GitHub Actions) and inject it into the job
  environment — never echo it.

## The plugin does not log tokens

The Notion token is treated as an opaque bearer credential. It is read at the
edge (`scripts/notion_sync.py`, `scripts/notion_client.py`) and only attached
to outbound HTTP headers — never written to stdout, stderr, graph state, or
the cursor file. The MCP contract is "structured JSON in, structured JSON out";
tool responses do not echo the token back.

If you are auditing this behavior yourself, read those two files directly
rather than relying on quoted snippets here — the exact call shape is liable
to drift.

## Rotation

Treat any leak — a token pasted into a chat, pushed to a branch, captured in a
CI log — as a full compromise. To rotate:

1. Go to <https://www.notion.so/my-integrations>, open the integration, and
   revoke or regenerate the secret.
2. Update the environment or secret manager with the new value.
3. Re-run any sync. The cursor (`data/notion_cursor.json`) is token-agnostic,
   so delta sync picks up where it left off.

## Scope

Grant the integration access only to the Notion databases and pages the plugin
actually needs to read. This limits blast radius if the token does leak and
keeps unrelated content out of the graph.
