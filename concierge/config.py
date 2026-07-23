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


def app_database_url() -> str:
    return get("APP_DATABASE_URL") or APP_DATABASE_URL


def owner_database_url() -> str:
    return get("DATABASE_URL") or OWNER_DATABASE_URL


# ---------------------------------------------------------------- email (Phase 4)
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


# ---------------------------------------------------------------- calendar (Phase 5)
#
# In the data model each tenant carries its own Cal.com in profile.calendar_ref{cal_api_key,
# event_type_id, ...}. These env fallbacks let the single-operator demo run without persisting a
# live key into a tenant's profile, and are read only when the profile does not carry its own.

def cal_api_key() -> str | None:
    return get("CAL_API_KEY")


def cal_event_type_id() -> str | None:
    return get("CAL_EVENT_TYPE_ID")
