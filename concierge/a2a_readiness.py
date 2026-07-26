"""Answerability check: `python3 -m concierge.a2a_readiness`.

Separate from `provision_worker` on purpose. That worker reports whether any buyer *was* served;
this reports whether one *could* be. On 2026-07-25 those two answers differed for nine hours and
nothing in the system was capable of noticing: the transport was up, the queue was empty because
the reviewer's job never reached our code, and the AI sessions the daemon spawned were dying on a
401. Every signal was green and the listing was rejected for not responding.

So this checks the three properties that have to hold simultaneously for the listing to be worth
having, and says which one broke:

  * the transport binary is ours and present   (`a2a.available`)
  * the daemon is up and ready                 (`a2a.healthy`)
  * the bound AI provider can authenticate     (`a2a.provider_auth_ok`)

Exit 0 only if all three hold. Anything else is a listing that looks online and cannot answer.

**And it tells a person.** A failing check used to reach the journal and nowhere else, which
reproduces the original failure in a quieter form: on 2026-07-26 the listing sat unanswerable for
26 minutes after a botched deploy and the only reason anyone found out was that someone happened
to run the probe by hand. `alert()` emails `ALERT_EMAIL` on the transition into not-answerable,
repeats at most every `REPEAT_HOURS` while it persists, and sends one recovery note when it comes
back — throttled through `STATE_PATH` so a 10-minute timer cannot become a mailbox full of the
same sentence. With no `ALERT_EMAIL` configured it says so on stderr and carries on: the check's
own verdict is never altered by whether the alert could be delivered, and no address is invented.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from . import a2a, config, postmark

# Six hours: long enough that a persistent outage does not bury the first alert under repeats,
# short enough that a failure starting overnight is still in the inbox by morning.
REPEAT_HOURS = 6

STATE_PATH = Path(os.environ.get("A2A_READINESS_STATE")
                  or (config.ROOT / ".a2a_readiness_state.json"))


def check() -> dict[str, object]:
    auth_ok, auth_detail = a2a.provider_auth_ok()
    transport = a2a.available()
    daemon = a2a.healthy()
    return {
        "checked_at": datetime.now(dt_timezone.utc).isoformat(),
        "transport_available": transport,
        "daemon_ready": daemon,
        "provider_auth_ok": auth_ok,
        "provider_detail": auth_detail,
        "answerable": bool(transport and daemon and auth_ok),
    }


def _read_state() -> dict[str, object]:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write_state(state: dict[str, object]) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state))
    except OSError:
        pass          # a probe that cannot write its own notes still reports the truth


def should_alert(report: dict[str, object], state: dict[str, object], now: datetime) -> str | None:
    """`"failing"`, `"recovered"`, or None. Pure — the caller does the sending.

    Split out from `alert` so the throttling can be proven without sending mail: the two rules
    that matter are "the first failure always alerts" and "the same failure does not alert every
    ten minutes forever".
    """
    was_answerable = state.get("answerable")
    if not report["answerable"]:
        if was_answerable is not False:
            return "failing"                    # first failure, or first since a recovery
        last = state.get("alerted_at")
        try:
            since = now - datetime.fromisoformat(str(last))
        except (TypeError, ValueError):
            return "failing"                    # no readable timestamp — err toward telling someone
        return "failing" if since >= timedelta(hours=REPEAT_HOURS) else None
    return "recovered" if was_answerable is False else None


def alert(report: dict[str, object], kind: str) -> str:
    """Send the alert. Returns a one-line description of what happened, for the journal."""
    to_address = config.get("ALERT_EMAIL")
    if not to_address:
        return ("ALERT NOT SENT: no ALERT_EMAIL configured. The check's verdict is unchanged; "
                "nobody has been told.")
    domain = config.inbound_domain()
    token = config.postmark_token()
    if not domain or not token:
        return ("ALERT NOT SENT: sending is not configured (CONCIERGE_DOMAIN/"
                "POSTMARK_SERVER_TOKEN). The check's verdict is unchanged.")
    failing = kind == "failing"
    subject = ("[CONCIERGE] A2A listing CANNOT ANSWER" if failing
               else "[CONCIERGE] A2A listing is answering again")
    body = (
        ("The A2A answerability check is FAILING. The listing may look online while being "
         "incapable of answering a buyer — this is the exact condition agent #9274 was rejected "
         "for on 2026-07-26.\n\n"
         if failing else
         "The A2A answerability check is passing again. No action needed.\n\n")
        + f"transport_available: {report['transport_available']}\n"
        + f"daemon_ready:        {report['daemon_ready']}\n"
        + f"provider_auth_ok:    {report['provider_auth_ok']}\n"
        + f"provider_detail:     {report['provider_detail']}\n"
        + f"checked_at:          {report['checked_at']}\n\n"
        + ("On the box: journalctl -u concierge-a2a-readiness -n 20\n"
           "            systemctl status concierge-a2a\n" if failing else "")
    )
    try:
        postmark.PostmarkMailer(token).send(postmark.OutboundEmail(
            from_address=f"alerts@{domain}", to_address=to_address,
            subject=subject, text_body=body))
        return f"alert sent to {to_address} ({kind})"
    except Exception as e:                       # noqa: BLE001 — an alert must never mask a verdict
        return f"ALERT NOT SENT: {type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    report = check()
    print(json.dumps(report), file=sys.stdout, flush=True)

    now = datetime.now(dt_timezone.utc)
    state = _read_state()
    kind = should_alert(report, state, now)
    if kind:
        print(alert(report, kind), file=sys.stderr, flush=True)
    _write_state({
        "answerable": report["answerable"],
        "alerted_at": now.isoformat() if kind == "failing" else state.get("alerted_at"),
        "last_checked_at": now.isoformat(),
    })

    if not report["answerable"]:
        # Loud on stderr as well as the JSON, because this is the line an operator greps for after
        # a reviewer says "your agent did not respond".
        print(f"NOT ANSWERABLE: transport={report['transport_available']} "
              f"daemon={report['daemon_ready']} provider={report['provider_detail']!r}",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
