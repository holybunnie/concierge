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
import re
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


# The platform's own event names, as they appear in a real payload. Confirmed against live
# traffic on 2026-07-25 — see `platform_events`.
KNOWN_EVENTS = (
    "sub_asp_selected", "sub_trial_into_active", "sub_failed_notify", "sub_cancelled",
    "job_asp_selected", "job_accepted", "job_delivered", "job_confirmed", "job_cancelled",
)

# Where the top-level `kind` is a *category* rather than an event name. Learned from live traffic:
# a real buyer message arrives as kind="notification", and the event name (`job_asp_selected`) is
# nested inside a stringified command several levels down.
#
# The backslash tolerance is not decoration. The real payload embeds JSON inside a shell command
# inside a JSON string, so by the time the name appears it is written `\\\"event\\\":\\\"…`, and a
# pattern expecting bare quotes finds nothing while looking like it works.
_EVENT_IN_TEXT = re.compile(r'\\*"event\\*"\s*:\s*\\*"([a-z_]+)')

# A real buyer's words arrive wrapped in corner brackets inside a rendered notification:
#   📥 [Received] SecAgent#1791 → CONCIERGE#9274 (you)\nJob: 0x4cb7…\n────\n「their message」\n────
# Everything outside the brackets is the platform's own chrome, addressed to a human reader.
_QUOTED = re.compile(r"「(.+?)」", re.S)

# The daemon echoes our own sent messages back into the same notification queue. Matched on the
# bracketed word rather than the emoji, which is decoration and may not survive every renderer.
_SENT_MARKER = re.compile(r"\[Sent\]")
_JOB_ACCEPTED_MARKER = re.compile(r"\[Job Accepted\].+?\bhas been accepted\b", re.I | re.S)
_RECEIVER_IN_HEADER = re.compile(r"\[Received\].+?→[^#\n]*#(\d+)\s+\(you\)", re.S)


@dataclass
class Event:
    """One marketplace notification, normalised.

    `kind` is what the payload calls itself at the top level. Note that this is NOT reliably the
    platform's event name: live traffic shows broad categories there (`notification`,
    `decision_request`) with the real event name buried in a nested string. Use `platform_events`
    to ask what actually happened, never `kind` alone.

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
            from_agent_id=_str_or_none(p.get("fromAgentId") or p.get("agentId")
                                       or _sender_from_text(p)),
            content=str(p.get("content") or p.get("userContent") or ""),
            raw=p,
        )

    def platform_events(self) -> set[str]:
        """Every known event name this payload mentions, wherever it is hiding.

        The top-level `kind` is checked first because that is where the documented shape puts it
        and where the gate suite's fixtures put it. Then the whole payload is searched as text,
        because that is where the *real* daemon puts it. Both, rather than either, so this reads
        the format the vendor documents AND the format it actually sends.
        """
        found = {self.kind} & set(KNOWN_EVENTS)
        for text in _strings(self.raw):
            found |= {m for m in _EVENT_IN_TEXT.findall(text) if m in KNOWN_EVENTS}
        return found

    def is_own_outbound(self) -> bool:
        """Is this notification an echo of a message WE sent?

        The daemon reports our own outbound traffic back through the same `user list` queue that
        carries inbound messages, distinguished only by a marker in the rendered header:

            📤 [Sent] CONCIERGE#9274 (you) → SandboxAgent#1791
            📥 [Received] SecAgent#1791 → CONCIERGE#9274 (you)

        Without this check every reply the worker sends is a message it will read next tick and
        try to answer — a loop with the buyer copied in on every turn. Today that would fail
        closed rather than loop, because an echo carries no sender for `xmtp-send` to address, but
        relying on a missing field to prevent a reply-storm is relying on an accident.
        """
        return bool(_SENT_MARKER.search(self.content))

    def receiving_agent_id(self) -> str | None:
        """Which local agent received this item, when the rendered header names one.

        One daemon can own both a User and ASP identity. A message received by the local User is
        still ``[Received]`` but must not be interpreted as input to the local ASP's tenant.
        """
        match = _RECEIVER_IN_HEADER.search(self.content)
        return match.group(1) if match else None

    def is_platform_internal(self) -> bool:
        """Is this the platform talking to US, rather than a buyer talking to us?

        `decision_request` items are the daemon asking its operator to pick from `choices` — a
        failed AI dispatch offering "retry / don't retry", for instance. Answering one by sending
        prose to the buyer would deliver our internal plumbing to a customer.
        """
        return self.kind == "decision_request" or bool(self.raw.get("choices"))

    def starts_tenant_engagement(self) -> bool:
        """Did the marketplace say this provider's one-off job is funded and accepted?

        OKX currently exposes this A2A service as per-job escrow, not as an Agent Seller
        subscription. The original provisioning path listened only for the older
        ``sub_asp_selected`` event and consequently ignored the real ``job_accepted`` wire event.

        The daemon's operator notification does not retain the event as a top-level field, so the
        rendered ``[Job Accepted]`` line is also a measured wire format, not a guessed fallback.
        """
        return ("job_accepted" in self.platform_events()
                or bool(_JOB_ACCEPTED_MARKER.search(self.content)))

    def message_text(self) -> str:
        """What the buyer actually wrote, with the platform's rendering stripped off.

        A real notification is a formatted block — arrows, agent ids, a truncated job id, divider
        rules — wrapping the message in corner brackets. Feeding all of that to a parser would
        have it reading the platform's chrome as the buyer's answer.
        """
        m = _QUOTED.search(self.content)
        return (m.group(1) if m else self.content).strip()


def _strings(node: Any) -> list[str]:
    """Every string anywhere in a payload, at any depth.

    Walking the structure rather than `json.dumps`-ing it keeps each embedded string at its own
    original escaping level instead of adding another one on the way past.
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _strings(v)]
    return []


def _sender_from_text(p: dict[str, Any]) -> str | None:
    """Recover the sending agent id from a rendered notification header.

    Live payloads carry no `fromAgentId` field; the sender appears only inside the human-readable
    line `📥 [Received] SecAgent#1791 → CONCIERGE#9274 (you)`. Reading it there is worth doing
    because it lets a reply be addressed explicitly rather than relying on the job id alone.
    """
    text = str(p.get("userContent") or p.get("content") or "")
    m = re.search(r"\[Received\][^#\n]*#(\d+)", text)
    return m.group(1) if m else None


def _str_or_none(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def binary() -> str:
    return config.get("OKX_A2A_BIN") or DEFAULT_BIN


def available() -> bool:
    """Is the transport usable at all? Used by the gate suite and by /healthz."""
    b = binary()
    return os.path.exists(b) or shutil.which(b) is not None


def healthy() -> bool:
    """Read the daemon's lock metadata without invoking a CLI that chmods its state directory."""
    try:
        home = os.environ.get("HOME", "/opt/concierge")
        lock = os.path.join(home, ".okx-agent-task", "run", "daemon.lock", "owner.json")
        with open(lock, encoding="utf-8") as f:
            metadata = json.load(f)
        pid = int(metadata["pid"])
        os.kill(pid, 0)
        return metadata.get("ready") is True or bool(metadata.get("readyAt"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


# The doctor line that reports whether the bound AI provider CLI can authenticate. Matched on the
# stable prefix rather than the whole sentence: the provider name and binary path both vary, and on
# a shared box they vary per user (running doctor as root reports a different provider entirely).
_PROVIDER_LINE = re.compile(r"AI provider CLI:\s*(.+)")

# The provider binary's own path, as doctor reports it ("claude CLI at /usr/local/bin/claude is
# logged in"). Read rather than assumed: the bound provider is configurable and is genuinely
# different per user on this box — running doctor as root reports `codex`, not `claude`.
_PROVIDER_BIN = re.compile(r"\bat\s+(/\S+)")

# What a provider CLI says when it cannot authenticate. `401`/`invalid` are the words from the
# three sessions that actually cost the listing; `not logged in` is what the CLI says when the
# credential is simply absent.
_AUTH_FAILURE = re.compile(
    r"not logged in|please run /login|api key is invalid|authentication_failed|401",
    re.I,
)


def provider_auth_ok() -> tuple[bool, str]:
    """Can the daemon's bound AI provider actually authenticate?

    This exists because of what "responsive" turned out not to mean on 2026-07-25. The daemon was
    up, heartbeating every 60s and reporting `onlineStatus: 1` the entire time, while all three of
    the reviewer's job sessions died on `401 API key is invalid` after three minutes each. Nothing
    anywhere went red. The listing was rejected for silence that every liveness signal we had was
    reporting as health.

    Liveness and the ability to answer are separate properties, and only the second one is the
    product. This checks the second.

    Returns `(ok, detail)` rather than raising: a caller wants to log the reason, and "doctor could
    not run" and "provider is logged out" are different operational facts that both mean the same
    thing to a buyer — nobody is going to answer them.
    """
    if not available():
        return False, f"okx-a2a not found at {binary()!r}"
    try:
        proc = subprocess.run(
            [binary(), "doctor"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except subprocess.TimeoutExpired:
        return False, f"okx-a2a doctor timed out after {TIMEOUT_S}s"

    match = _PROVIDER_LINE.search(proc.stdout)
    if not match:
        return False, f"okx-a2a doctor reported no AI provider line (exit {proc.returncode})"
    detail = match.group(1).strip()
    # Doctor states the negative explicitly ("… is not logged in"), so the negative is what we
    # test. Treating "contains 'logged in'" as success would pass on exactly the failing string.
    if "not logged in" in detail or "logged in" not in detail:
        return False, detail

    # Doctor saying "logged in" is NOT sufficient, and this is the whole reason this function is
    # not three lines long. Measured on 2026-07-26: with the credential removed from the
    # environment, doctor still reported `claude CLI at /usr/local/bin/claude is logged in` while
    # that very binary answered `Not logged in · Please run /login`. Doctor inspects stored
    # credentials; a job session needs a credential that actually authenticates, and those are
    # different questions. Believing doctor is how a 401 stays invisible for nine hours.
    #
    # So we ask the provider itself, with the smallest prompt that still requires a round trip.
    bin_match = _PROVIDER_BIN.search(detail)
    if not bin_match:
        return True, f"{detail} (unprobed: could not read provider path)"
    provider_bin = bin_match.group(1)
    try:
        probe = subprocess.run(
            [provider_bin, "-p", "ping"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"{detail} but probe failed to run: {type(exc).__name__}: {exc}"

    combined = f"{probe.stdout}\n{probe.stderr}"
    if _AUTH_FAILURE.search(combined):
        # The model declining to answer is not an auth failure, and must not be read as one — only
        # the auth markers count. A refusal still proves the credential worked.
        return False, f"{detail} but probe could not authenticate: {combined.strip()[:200]}"
    if probe.returncode != 0:
        return False, f"{detail} but probe exited {probe.returncode}: {combined.strip()[:200]}"
    return True, f"{detail} (probe authenticated)"


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

    **This is `xmtp-send`, and the distinction is not cosmetic.** The obvious-looking
    `session send --content` is *AI dispatch*: it injects text into the local AI subsession as
    though it had arrived from the peer, and the CLI's own help says so — "Manage session metadata
    and AI dispatch". Calling it to answer a buyer does not answer the buyer. It hands our reply to
    the bound LLM as a prompt, and the buyer hears nothing at all.

    That mistake was live on 2026-07-25 and cost a real message to a real agent: `session send`
    exited 0, the notification was consumed as handled, and the only trace of the reply was our own
    prose sitting in `~/.okx-agent-task/logs/llm.log` while the provider 401'd. An exit code is not
    a delivery. `xmtp-send` is the one that queues a message through the daemon to the peer.
    """
    args = ["xmtp-send", "--job-id", job_id, "--message", content]
    if to_agent_id:
        args += ["--to-agent-id", to_agent_id]
    else:
        # xmtp-send addresses a peer, not a job: without a recipient there is nobody to deliver
        # to. Raising beats letting the CLI fail in a way a caller might read as "nothing to do".
        raise A2AUnavailable(
            f"Refusing to send on job {job_id!r} with no recipient agent id — xmtp-send requires "
            f"--to-agent-id, and a message with no addressee is not a message."
        )
    _run(args)


def deliver(job_id: str, content: str, *, provider_agent_id: str = "9274") -> None:
    """Submit completed onboarding; argv is direct so tenant text is never executable."""
    binary = config.get("ONCHAINOS_BIN") or "/usr/local/bin/onchainos"
    try:
        proc = subprocess.run(
            [binary, "agent", "deliver", job_id, "--file", "",
             "--agent-id", provider_agent_id, "--deliverable-text", content],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise A2AUnavailable(f"Could not deliver job {job_id!r}: {exc}") from exc
    if proc.returncode != 0:
        # A remote success followed by a local crash replays here. Treat a task the platform
        # already calls submitted/completed as success rather than attempting a second delivery.
        if _task_status(job_id, provider_agent_id) in {"submitted", "completed"}:
            return
        raise A2AUnavailable(
            f"onchainos agent deliver failed for job {job_id!r}: "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )


def _task_status(job_id: str, provider_agent_id: str) -> str | None:
    binary = config.get("ONCHAINOS_BIN") or "/usr/local/bin/onchainos"
    try:
        proc = subprocess.run(
            [binary, "agent", "status", job_id, "--agent-id", provider_agent_id],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = re.search(r"^Task status:\s*([a-z_]+)\s*$", proc.stdout, re.M | re.I)
    return match.group(1).lower() if proc.returncode == 0 and match else None


def task_participants(job_id: str, *, provider_agent_id: str = "9274") -> tuple[str, str]:
    """Return ``(buyer_agent_id, provider_agent_id)`` from the marketplace task record.

    System-generated lifecycle notifications carry a job id but no sender. Guessing the buyer
    would cross tenant boundaries; dropping the event leaves a paid customer unprovisioned.
    ``onchainos agent status`` is the supported source of truth and prints stable ``user``/``asp``
    fields even when the notification omitted both.
    """
    binary = config.get("ONCHAINOS_BIN") or "/usr/local/bin/onchainos"
    try:
        proc = subprocess.run(
            [binary, "agent", "status", job_id, "--agent-id", provider_agent_id],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "HOME": os.environ.get("HOME", "/opt/concierge")},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise A2AUnavailable(f"Could not resolve participants for job {job_id!r}: {exc}") from exc
    if proc.returncode != 0:
        raise A2AUnavailable(
            f"onchainos agent status failed for job {job_id!r}: {proc.stderr.strip()[:400]}"
        )
    buyer = re.search(r"^\s*user:\s*(\d+)\s*$", proc.stdout, re.M)
    provider = re.search(r"^\s*asp:\s*(\d+)\s*$", proc.stdout, re.M)
    if not buyer or not provider:
        raise A2AUnavailable(
            f"Task status for job {job_id!r} omitted user/asp participants: {proc.stdout[:400]!r}"
        )
    return buyer.group(1), provider.group(1)
