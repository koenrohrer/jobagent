# jobagent

[![CI](https://github.com/koenrohrer/jobagent/actions/workflows/ci.yml/badge.svg)](https://github.com/koenrohrer/jobagent/actions/workflows/ci.yml)

MCP server for managing a personal job-application workflow. Plugs into
Claude Code, Claude Desktop, Cursor, or any MCP client.

You hand it a job-posting URL, it scrapes the title, guesses the company,
and tracks the application's status through your pipeline
(`interested → applied → oa → phone → onsite → offer / rejected / ghosted`)
in a local SQLite file. The agent can then list, update, and (eventually)
draft follow-ups.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/koenrohrer/jobagent
cd jobagent
uv sync
```

This is the recommended workflow because it lets the MCP server run
straight from the checkout. If you'd rather install as a package:

```bash
uv build              # produces dist/jobagent-*.whl
pip install dist/jobagent-*.whl
```

## Wire it up to your MCP client

### Claude Code

```bash
claude mcp add jobagent uv --directory "$(pwd)" run python server.py
```

Restart Claude Code. Tools appear under the `mcp__jobagent__*` namespace.

### Claude Desktop / Cursor / generic JSON config

Add to your client's MCP servers config (e.g.
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "jobagent": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/jobagent", "run", "python", "server.py"]
    }
  }
}
```

### Run standalone (for inspection or testing)

```bash
uv run python server.py            # speaks stdio
```

Inspect interactively with the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run python server.py
```

## Tools

All tools live under the `jobagent` server. From an agent's perspective:

| Tool | What it does |
|---|---|
| `add_application(url)` | Fetches the page, extracts `<title>` / `<h1>`, guesses the company, inserts with status `interested`. Returns an `AddApplicationResult` (the row plus `deduped: bool` — true if the URL was already tracked). |
| `list_applications(status?, limit=50)` | Lists tracked applications, most-recently-updated first. Optional status filter. Limit capped at 500. |
| `update_status(application_id, status, note?)` | Changes status (and optionally appends a note). Records a `status_changed` event in the audit log. Idempotent: a call with the same status and no note is a no-op. |
| `find_followups()` | _TBD._ Stale `applied` / `oa` / `phone` rows that need a nudge. |
| `draft_followup(application_id)` | _TBD._ Subject + body draft for a follow-up email. |
| `sync_recruiter_emails()` | _TBD._ Pulls new Gmail threads under the `jobs` label and links them to applications. |

### Working with the agent

Once the server is wired up, you can talk to your MCP client in plain
English — the agent decides which tools to call. Some patterns that work
well:

- _"Track this job: https://jobs.lever.co/acme/abc-123"_ → `add_application`
- _"Show me everything in the OA stage."_ → `list_applications(status="oa")`
- _"Mark application 7 as 'phone' and note that the recruiter call is Tuesday at 2pm."_ → `update_status(7, "phone", note="...")`
- _"What did I apply to this week?"_ → `list_applications` + the agent filters by `created_at`.

## Status values

```
interested  applied  oa  phone  onsite  offer  rejected  ghosted
```

Enforced both at the Pydantic boundary (so the MCP client rejects invalid
input) and at the SQLite layer via a `CHECK` constraint (so a sibling tool
or manual `sqlite3` session can't poison the table).

## Storage

SQLite at `~/.local/share/jobagent/jobagent.db` by default. Override:

```bash
export JOBAGENT_DB=/path/to/jobagent.db
```

The schema migrates itself on startup — if you upgraded from an earlier
version that didn't have the status `CHECK` constraint, `db.init()`
rebuilds the table in place (rows preserved, transactional).

## Security notes

`add_application` fetches an arbitrary user-supplied URL, which is an
inherent SSRF risk. The server defends against the common attack shapes:

- All requests go through a custom httpx transport
  (`_PinnedResolverTransport`) that resolves the hostname once, validates
  the IP is publicly routable (rejects loopback, RFC1918, link-local,
  cloud-metadata `169.254.169.254`, multicast, reserved), then connects
  to the literal IP with `Host:` header and TLS SNI preserved.
- This single-resolve-then-pin approach closes the DNS-rebinding TOCTOU
  window between validation and the actual TCP connect.
- The transport runs on every redirect hop, so a public URL that 302s to
  a private address is blocked at the redirect rather than being
  followed.
- Blocked attempts are logged at WARNING so you can spot probing.

## Tests

```bash
uv run pytest -q
```

Branch coverage is gated at 100% (`pytest-cov`, `--cov-fail-under=100`).
Each test gets an isolated SQLite DB via the `app_db` fixture (see
`tests/conftest.py`), so the suite never touches your real data.

One integration test stands up a local `http.server` to verify the
SSRF transport's URL/Host/SNI rewriting works end-to-end through real
httpx; everything else uses fast in-process mocks.
