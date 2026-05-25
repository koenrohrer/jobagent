"""jobagent MCP server.

Exposes job-application workflow tools over MCP (stdio transport by default).
Install in Claude Code via:

    claude mcp add jobagent uv --directory "$(pwd)" run python server.py
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

import applications
import db

Status = Literal[
    "interested",
    "applied",
    "oa",
    "phone",
    "onsite",
    "offer",
    "rejected",
    "ghosted",
]

mcp = FastMCP("jobagent")


class Application(BaseModel):
    id: int
    url: str
    company: str | None
    role: str | None
    title: str | None
    status: Status
    notes: str | None
    created_at: str
    updated_at: str
    last_touched_at: str | None = None


class AddApplicationResult(Application):
    deduped: bool = Field(
        description="True if URL was already tracked; existing row was returned.",
    )


class FollowupCandidate(BaseModel):
    application: Application
    days_since_last_touch: int
    suggested_action: str


class FollowupDraft(BaseModel):
    application_id: int
    subject: str
    body: str


class GmailSyncResult(BaseModel):
    new_threads: int
    linked_to_existing: int
    unlinked: int


@mcp.tool()
def add_application(url: str) -> AddApplicationResult:
    """Add a job application by URL.

    Fetches the page, extracts the <title> tag, guesses the company
    from the domain, and stores the row with status 'interested'. If
    the URL is already tracked, returns the existing row with
    deduped=True instead of inserting a duplicate.

    Args:
        url: Full http(s) URL of the job posting.
    """
    return AddApplicationResult(**applications.add(url))


@mcp.tool()
def list_applications(
    status: Status | None = None,
    limit: int = 50,
) -> list[Application]:
    """List tracked applications, most recently updated first.

    Args:
        status: Optional status filter. Omit to see all.
        limit: Max rows to return (default 50, cap 500).
    """
    rows = applications.list_(status=status, limit=limit)
    return [Application(**r) for r in rows]


@mcp.tool()
def update_status(
    application_id: int,
    status: Status,
    note: str | None = None,
) -> Application:
    """Change an application's status, optionally appending a note.

    Args:
        application_id: The application's id.
        status: One of interested, applied, oa, phone, onsite, offer,
            rejected, ghosted.
        note: Optional free-text note to append to the row.
    """
    return Application(**applications.update_status(application_id, status, note))


@mcp.tool()
def find_followups() -> list[FollowupCandidate]:
    """List applications that probably need a nudge.

    Heuristic: 'applied' rows untouched >10 days, 'oa'/'phone' rows
    untouched >5 days. Returns them ordered by staleness.
    """
    raise NotImplementedError("find_followups — implement in week 2")


@mcp.tool()
def draft_followup(application_id: int) -> FollowupDraft:
    """Generate a follow-up email template for an application.

    Returns subject + body the agent can refine. The agent should
    call list_applications first to gather context for the body.

    Args:
        application_id: The application's id.
    """
    raise NotImplementedError("draft_followup — implement in week 2")


@mcp.tool()
def sync_recruiter_emails() -> GmailSyncResult:
    """Pull new messages from the Gmail 'jobs' label and link to applications.

    Requires Gmail OAuth setup — see README. Idempotent.
    """
    raise NotImplementedError(
        "sync_recruiter_emails — wire Gmail OAuth first (week 2)"
    )


def main() -> None:
    db.init()
    mcp.run()


if __name__ == "__main__":
    main()
