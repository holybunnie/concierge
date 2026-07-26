"""Deterministic commercial pricing for CONCIERGE's OKX A2A engagement.

The marketplace currently represents each 30-day term as one escrowed job. The first ten
distinct buyers may reserve one launch engagement at 2.5 USDT; all later and repeat engagements
are 10 USDT. Reservation and the ten-buyer boundary live in PostgreSQL, not in an AI prompt.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from . import db


def claim(job_id: str, buyer_agent_id: str, amount: str, currency: str) -> dict[str, Any]:
    try:
        offered = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("amount must be a decimal number") from exc
    if not offered.is_finite() or offered < 0:
        raise ValueError("amount must be a finite non-negative number")

    with db.unscoped_session() as cur:
        cur.execute(
            "SELECT * FROM claim_marketplace_price(%s, %s, %s, %s)",
            (job_id, buyer_agent_id, offered, currency),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError("pricing function returned no decision")
    result = dict(row)
    result["required_price"] = format(result["required_price"], "f").rstrip("0").rstrip(".")
    result["term_days"] = 30
    result["currency"] = "USDT"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and reserve an OKX marketplace price")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--buyer-agent-id", required=True)
    parser.add_argument("--amount", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()
    try:
        decision = claim(args.job_id, args.buyer_agent_id, args.amount, args.currency)
    except Exception as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps(decision, separators=(",", ":"), sort_keys=True))
    return 0 if decision["accepted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
