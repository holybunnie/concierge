"""The systemd entry point for auto-provisioning: `python3 -m concierge.provision_worker`.

`provision.process_pending()` is the whole loop body; this module exists only to be the thing a
timer can execute, and to say — in one greppable line — what happened. It deliberately holds no
logic of its own. Anything decided here would be a decision the gate suite never sees, because the
suite drives `provision.process_pending` directly.

Two behaviours are worth knowing before changing this file.

**A transport outage is an error, not an empty inbox.** `a2a.pending_events` raises
`A2AUnavailable` rather than returning `[]` when the CLI is missing or the daemon is down, and this
module reports that as a non-zero exit with the reason attached. The failure mode being avoided is
a worker that runs every minute for a week, finds nothing because it cannot see anything, and looks
healthy the entire time — while every buyer who subscribed is waiting for a first question that is
never coming.

**Exit 0 with `seen: 0` is the normal quiet case** and must stay distinguishable from the above.
Nobody subscribed in the last minute is the expected state of a marketplace listing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone as dt_timezone

from . import a2a, provision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="concierge.provision_worker", description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report whether the transport is reachable and exit, touching no events")
    args = parser.parse_args(argv)

    started = datetime.now(dt_timezone.utc)
    report: dict[str, object] = {"started_at": started.isoformat()}

    if args.check:
        report.update(mode="check", transport_available=a2a.available(), binary=a2a.binary())
        _emit(report, started)
        return 0 if a2a.available() else 1

    try:
        report.update(provision.process_pending())
    except a2a.A2AUnavailable as exc:
        # Loud, and specific about which of the several causes it was — the CLI's own stderr is
        # carried through rather than flattened into "transport error".
        report.update(seen=0, transport_error=f"{type(exc).__name__}: {exc}")
        _emit(report, started)
        return 1

    _emit(report, started)
    return 1 if report.get("failed") else 0


def _emit(report: dict[str, object], started: datetime) -> None:
    """One JSON line per run to stdout, so `journalctl -u concierge-a2a-provision` is machine
    -readable rather than prose a human has to read to find out whether a buyer was served."""
    report["finished_at"] = datetime.now(dt_timezone.utc).isoformat()
    print(json.dumps(report), file=sys.stdout, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
