"""Verification for the finite OKX launch-price policy."""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from . import config


def run(r) -> None:
    prefix = f"verify-{uuid.uuid4().hex}"
    with psycopg.connect(config.owner_database_url(), row_factory=dict_row) as conn:
        # Roll the entire fixture back: verification must not consume a real promotional slot.
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()

            mismatch = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-bad", f"{prefix}-buyer-1", "1", "USDT"),
            ).fetchone()
            count_after_bad = cur.execute(
                "SELECT count(*) AS n FROM marketplace_engagements WHERE job_id LIKE %s",
                (f"{prefix}%",),
            ).fetchone()["n"]
            r.check(
                "A low offer is rejected without consuming the promotion",
                not mismatch["accepted"] and mismatch["required_price"] == 2.5
                and count_after_bad == 0,
                f"required={mismatch['required_price']} accepted={mismatch['accepted']} "
                f"rows_reserved={count_after_bad}",
            )

            first_ten = []
            for n in range(1, 11):
                first_ten.append(cur.execute(
                    "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                    (f"{prefix}-job-{n}", f"{prefix}-buyer-{n}", "2.5", "USDT"),
                ).fetchone())
            r.check(
                "Exactly ten distinct buyers receive 2.5 USDT",
                all(x["accepted"] and x["is_promo"] and x["required_price"] == 2.5
                    for x in first_ten)
                and [x["promo_number"] for x in first_ten] == list(range(1, 11)),
                f"promo_numbers={[x['promo_number'] for x in first_ten]}",
            )

            eleventh_low = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-job-11-low", f"{prefix}-buyer-11", "2.5", "USDT"),
            ).fetchone()
            eleventh = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-job-11", f"{prefix}-buyer-11", "10", "USDT"),
            ).fetchone()
            r.check(
                "Buyer eleven must pay 10 USDT",
                not eleventh_low["accepted"] and eleventh_low["required_price"] == 10
                and eleventh["accepted"] and not eleventh["is_promo"],
                f"2.5_offer={eleventh_low['reason']} required={eleventh_low['required_price']}; "
                f"10_offer_accepted={eleventh['accepted']}",
            )

            repeat = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-repeat", f"{prefix}-buyer-1", "10", "USDT"),
            ).fetchone()
            wrong_currency = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-currency", f"{prefix}-buyer-12", "10", "USDG"),
            ).fetchone()
            r.check(
                "A repeat buyer is full price and only USDT is accepted",
                repeat["accepted"] and repeat["required_price"] == 10
                and not wrong_currency["accepted"]
                and wrong_currency["reason"] == "currency_must_be_usdt",
                f"repeat_required={repeat['required_price']}; "
                f"USDG_result={wrong_currency['reason']}",
            )

            replay = cur.execute(
                "SELECT * FROM claim_marketplace_price(%s,%s,%s,%s)",
                (f"{prefix}-job-1", f"{prefix}-buyer-1", "2.5", "USDT"),
            ).fetchone()
            lock_present = cur.execute(
                "SELECT pg_get_functiondef('claim_marketplace_price(text,text,numeric,text)'::regprocedure) AS src"
            ).fetchone()["src"]
            r.check(
                "Replays are idempotent and the ten-slot boundary is transaction-locked",
                replay["accepted"] and replay["reason"] == "already_reserved"
                and "pg_advisory_xact_lock" in lock_present,
                f"replay={replay['reason']}; advisory_lock="
                f"{'pg_advisory_xact_lock' in lock_present}",
            )

    with psycopg.connect(config.app_database_url()) as conn:
        direct_table_denied = False
        try:
            conn.execute("SELECT * FROM marketplace_engagements")
        except psycopg.errors.InsufficientPrivilege:
            direct_table_denied = True
    r.check(
        "The web application cannot read or edit the global buyer registry",
        direct_table_denied,
        f"direct_table_access_denied={direct_table_denied}; only the narrow claim function is granted",
    )
