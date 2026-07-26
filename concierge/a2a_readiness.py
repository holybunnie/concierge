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
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone as dt_timezone

from . import a2a


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


def main(argv: list[str] | None = None) -> int:
    report = check()
    print(json.dumps(report), file=sys.stdout, flush=True)
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
