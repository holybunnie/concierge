"""The OKX A2A transport — a delivery channel, exactly like `postmark.py`.

This module shells out to the `okx-a2a` CLI and parses its JSON. That is the whole of its job.
It carries messages in and out; it decides nothing. In particular it never reads a profile, never
computes a price, and never chooses what to say — `provision.py` and `engine.py` do that, from
stored tenant data, the same way they do on the email path.

Why a subprocess rather than a library: the daemon owns the XMTP identity and its SQLite state
under `$HOME/.okx-agent-task`, and the CLI is the only supported way into it. Talking to that
store directly would mean two writers to one database.

Two environment facts this module depends on, both learned the hard way on the shared box and
both pinned in `deploy/concierge-a2a.service`:

  * `OKX_A2A_BIN` must point at OUR copy of the CLI, not the global `/usr/local/bin/okx-a2a`.
    The rwoo project's running daemon executes the global one.
  * The CLI hard-requires Node 22+ (it imports `node:sqlite`). `/usr/local/bin/node` is 22.x;
    `/usr/bin/node` is 20.x and dies on import. PATH order is what keeps this working.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import config

# Where our private CLI lives on the VPS. Overridable so a test box or a future path change does
# not require a code edit, but it deliberately does NOT fall back to a bare `okx-a2a` PATH lookup:
# on the shared box that resolves to another project's binary.
DEFAULT_BIN = "/opt/concierge/a2a/node_modules/.bin/okx-a2a"

TIMEOUT_S = 60


class A2AUnavailable(RuntimeError):
    """The CLI is missing, not logged in, or the daemon is not running.

    Raised rather than returning empty, so a provisioning worker that cannot reach the
    marketplace reports an outage instead of silently concluding there are no new buyers.
    """


@dataclass
class Event:
    """One marketplace notification, normalised.

    `kind` is the platform's own event name (`sub_asp_selected`, `sub_trial_into_active`, …).
    `raw` keeps the entire original payload: the platform adds fields over time and we would
    rather carry an unrecognised one around than drop it.
    """

    todo_id: str
    kind: str
    job_id: str | None
    from_agent_id: str | None
    content: str
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, p: dict[str, Any]) -> "Event":
        return cls(
            todo_id=str(p.get("todoId") or p.get("id") or ""),
            kind=str(p.get("type") or p.get("event") or p.get("kind") or "unknown"),
            job_id=_str_or_none(p.get("jobId") or p.get("job_id")),
            from_agent_id=_str_or_none(p.get("fromAgentId") or p.get("agentId")),
            content=str(p.get("content") or p.get("userContent") or ""),
            raw=p,
        )


def _str_or_none(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def binary() -> str:
    return config.get("OKX_A2A_BIN") or DEFAULT_BIN


def available() -> bool:
    """Is the transport usable at all? Used by the gate suite and by /healthz."""
    b = binary()
    return os.path.exists(b) or shutil.which(b) is not None


def _run(args: list[str]) -> dict[str, Any] | list[Any]:
    if not available():
        raise A2AUnavailable(
            f"okx-a2a not found at {binary()!r}. Set OKX_A2A_BIN, and do NOT fall back to the "
            f"global /usr/local/bin/okx-a2a — that binary belongs to another project on this box."
        )
    try:
        proc = subprocess.run(
            [binary(), *args, "--json"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except subprocess.TimeoutExpired as exc:
        raise A2AUnavailable(f"okx-a2a {' '.join(args)} timed out after {TIMEOUT_S}s") from exc

    if proc.returncode != 0:
        raise A2AUnavailable(
            f"okx-a2a {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise A2AUnavailable(f"okx-a2a {' '.join(args)} returned non-JSON: {out[:200]!r}") from exc


def pending_events() -> list[Event]:
    """Unhandled marketplace notifications, oldest first.

    `user list` is a read — it does not mark anything handled. Nothing is consumed until
    provisioning has actually committed a tenant, so a crash mid-provision replays the event
    rather than losing the buyer.
    """
    payload = _run(["user", "list"])
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    return [Event.from_payload(p) for p in items if isinstance(p, dict)]


def consume(todo_id: str) -> None:
    """Mark one notification handled. Called only after the database transaction has committed."""
    _run(["user", "check", "--todo-ids", todo_id])


def send(job_id: str, content: str, *, to_agent_id: str | None = None) -> None:
    """Deliver a message back to the buying agent over XMTP.

    The content is produced by our own code and passed through verbatim. There is no model in
    this path — see the module docstring.
    """
    args = ["session", "send", "--job-id", job_id, "--content", content]
    if to_agent_id:
        args += ["--to-agent-id", to_agent_id]
    _run(args)
