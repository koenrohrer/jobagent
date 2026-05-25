# jobagent

[![CI](https://github.com/koenrohrer/jobagent/actions/workflows/ci.yml/badge.svg)](https://github.com/koenrohrer/jobagent/actions/workflows/ci.yml)

MCP server for managing a personal job-application workflow. Plugs into
Claude Code, Claude Desktop, Cursor, or any MCP client.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Install in Claude Code

```bash
claude mcp add jobagent uv --directory "$(pwd)" run python server.py
```

Restart Claude Code. The tool `mcp__jobagent__add_application` (and
sibling tools) will appear.

## Run for local testing

```bash
uv run python server.py            # speaks stdio
```

Inspect with the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run python server.py
```

## Storage

SQLite at `~/.local/share/jobagent/jobagent.db` by default. Override:

```bash
export JOBAGENT_DB=/path/to/jobagent.db
```

## Tool status

- [x] `add_application` — scrape URL, dedupe, store
- [x] `list_applications` — filter by status, ordered by recency
- [x] `update_status` — change status, append notes, record event
- [ ] `find_followups` — week 2
- [ ] `draft_followup` — week 2
- [ ] `sync_recruiter_emails` — week 2 (Gmail OAuth)

## Tests

```bash
uv run pytest -q
```

Each test gets an isolated SQLite DB via the `app_db` fixture (see
`tests/conftest.py`), so the suite never touches your real data.
