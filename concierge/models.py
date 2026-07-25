"""Domain objects (§8). Plain dataclasses — the database is the source of truth, not an ORM."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Thread states (§9). A thread is always in exactly one of these.
# AWAITING_OWNER_APPROVAL (Feature 2, the autonomy suite): a reply was drafted but scored below the
# tenant's autonomy threshold for that service, so it is held for the owner rather than sent.
STATES = ("NEW", "ENGAGED", "AWAITING_REPLY", "NEGOTIATING", "BOOKED", "ESCALATED", "IGNORED",
          "DEAD", "AWAITING_OWNER_APPROVAL")


@dataclass
class Tenant:
    tenant_id: uuid.UUID
    owner_wallet: str
    owner_email: str
    business_name: str
    vertical: str
    inbound_address: str
    profile: dict[str, Any] = field(default_factory=dict)
    engagement: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Tenant":
        return cls(**{k: row[k] for k in (
            "tenant_id", "owner_wallet", "owner_email", "business_name", "vertical",
            "inbound_address", "profile", "engagement", "created_at",
        )})


@dataclass
class Thread:
    thread_id: uuid.UUID
    tenant_id: uuid.UUID
    client_contact: str
    state: str
    client_name: str | None = None
    client_timezone: str | None = None
    external_ref: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    current_offer: dict[str, Any] | None = None
    offered_slots: list[dict[str, Any]] = field(default_factory=list)
    last_updated: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Thread":
        return cls(**{k: row[k] for k in (
            "thread_id", "tenant_id", "client_contact", "state", "client_name",
            "client_timezone", "external_ref", "history", "current_offer", "offered_slots",
            "last_updated", "created_at",
        )})


@dataclass
class Receipt:
    receipt_id: uuid.UUID
    tenant_id: uuid.UUID
    thread_id: uuid.UUID | None
    action: str
    decision: dict[str, Any]
    rule_checked: str
    within_rules: bool
    content_hash: str
    signature: str | None = None
    xlayer_tx: str | None = None
    confidence: dict[str, Any] | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Receipt":
        return cls(**{k: row[k] for k in (
            "receipt_id", "tenant_id", "thread_id", "action", "decision", "rule_checked",
            "within_rules", "content_hash", "signature", "xlayer_tx", "confidence", "created_at",
        )})


@dataclass
class GapEvent:
    """Feature 1 (Product-Gap Intelligence) — one inquiry the tenant's profile could not
    answer. Written as a side effect of the existing ESCALATE transition (§ Unquotable),
    never a decision of its own."""

    gap_id: uuid.UUID
    tenant_id: uuid.UUID
    thread_id: uuid.UUID | None
    raw_query_text: str
    classified_category: str | None = None
    escalated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "GapEvent":
        return cls(**{k: row[k] for k in (
            "gap_id", "tenant_id", "thread_id", "raw_query_text", "classified_category",
            "escalated_at",
        )})
