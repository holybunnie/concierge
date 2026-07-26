"""Configuration. Reads .env if present; never invents a value.

A missing credential is returned as None and reported as missing by whoever needed it.
There are no placeholder keys anywhere in this codebase (§3).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_loaded = False


def load_env() -> None:
    """Merge .env into os.environ without overriding anything already set."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get(name: str) -> str | None:
    load_env()
    value = os.environ.get(name)
    return value or None


def enabled(name: str, *, default: bool = False) -> bool:
    value = get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def require(name: str, needed_for: str) -> str:
    """Raise loudly rather than proceeding with a fabricated value."""
    value = get(name)
    if not value:
        raise MissingCredential(
            f"{name} is not set. It is required for: {needed_for}. "
            f"Add it to .env — see docs/OPERATOR_PROVIDES.md. "
            f"CONCIERGE will not substitute a placeholder."
        )
    return value


class MissingCredential(RuntimeError):
    pass


# The application always connects as the unprivileged, RLS-bound role. The owner URL is used
# only to run migrations, and never by request-handling code.
APP_DATABASE_URL = "postgresql://concierge_app:concierge_app@localhost:5432/concierge"
OWNER_DATABASE_URL = "postgresql://concierge:concierge@localhost:5432/concierge"
# The scheduled worker's enumeration-only role (the scheduler). It holds no table grants at all — see
# the worker-role block in schema.sql for why it is separate from concierge_app.
WORKER_DATABASE_URL = "postgresql://concierge_worker:concierge_worker@localhost:5432/concierge"


def app_database_url() -> str:
    return get("APP_DATABASE_URL") or APP_DATABASE_URL


def owner_database_url() -> str:
    return get("DATABASE_URL") or OWNER_DATABASE_URL


def worker_database_url() -> str:
    return get("WORKER_DATABASE_URL") or WORKER_DATABASE_URL


# ---------------------------------------------------------------- email (the email connector)
#
# Inbound mail lands on a dedicated subdomain — inbox.<domain> — so parsing incoming mail can
# never interfere with normal mail on the apex, and so the MX that points at Postmark's inbound
# server sits on a host of its own (§0, verified). Tenant addresses are therefore
# <slug>@inbox.<domain>. Until the operator provides the domain (item 2), this returns None and
# onboarding falls back to PENDING-DOMAIN.invalid, which can never resolve.

def inbound_domain() -> str | None:
    base = get("CONCIERGE_DOMAIN")
    return f"inbox.{base}" if base else None


def postmark_token() -> str | None:
    """The server API token; one token covers inbound parse and outbound send."""
    return get("POSTMARK_SERVER_TOKEN")


def inbound_webhook_secret() -> str | None:
    """Shared secret the inbound webhook authenticates with.

    Postmark authenticates inbound webhooks with HTTP Basic Auth carried in the webhook URL
    (there is no HMAC signature on inbound — see docs/VERIFICATION_LEDGER.md). This is the
    password half. Absent → the webhook fails closed and accepts nothing.
    """
    return get("POSTMARK_INBOUND_WEBHOOK_SECRET")


# ---------------------------------------------------------------- calendar (booking)
#
# In the data model each tenant carries its own Cal.com in profile.calendar_ref{cal_api_key,
# event_type_id, ...}. These env fallbacks let the single-operator demo run without persisting a
# live key into a tenant's profile, and are read only when the profile does not carry its own.

def cal_api_key() -> str | None:
    return get("CAL_API_KEY")


def cal_event_type_id() -> str | None:
    return get("CAL_EVENT_TYPE_ID")


# ---------------------------------------------------------------- chain (receipt anchoring)
#
# X Layer mainnet only (§9, docs/VERIFICATION_LEDGER.md) — a testnet receipt proves nothing to a
# customer or an arbitrator. XLAYER_CONTRACT is written once, at deploy time, by the operator
# (or by the deploy script into this same .env) — it is not something CONCIERGE code invents.

XLAYER_MAINNET_RPC = "https://rpc.xlayer.tech"
XLAYER_MAINNET_CHAIN_ID = 196


def xlayer_rpc() -> str:
    return get("XLAYER_RPC") or XLAYER_MAINNET_RPC


def xlayer_private_key() -> str | None:
    return get("XLAYER_PRIVATE_KEY")


def xlayer_explorer_tx_url(tx_hash: str) -> str:
    """OKLink's X Layer transaction page — the RESOLVED path, not the naive `/xlayer/tx/` guess,
    which 301-redirects here (docs/VERIFICATION_LEDGER.md, Feature 3, verified live 2026-07-24).
    """
    return f"https://www.oklink.com/x-layer/evm/tx/{tx_hash}"


# ---------------------------------------------------------------- public verification (Feature 3)
#
# `verify_public_receipts.py` / the public-receipt suite. The page itself needs no credential to exist — it reads receipts
# already in Postgres — but the LINK in an outbound email needs a real, reachable base URL to
# point at. Absent, no verify line is appended (§3: never a placeholder that looks live), the
# same honest degradation as PENDING-DOMAIN.invalid for a tenant address with no domain yet.

def public_base_url() -> str | None:
    explicit = get("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    domain = get("CONCIERGE_DOMAIN")
    return f"https://app.{domain}" if domain else None


def xlayer_contract() -> str | None:
    return get("XLAYER_CONTRACT")
