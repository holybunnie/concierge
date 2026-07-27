"""The always-on inbound webhook (§12). This is the process systemd keeps alive on the VPS.

One job: receive Postmark's inbound POST, authenticate it, hand it to `concierge.mail`, and
answer 2xx so Postmark does not retry for three days. It holds no business logic — every
decision belongs to the engine, reached through `mail.handle_inbound`.

Run locally:   uvicorn concierge.app:app --host 0.0.0.0 --port 8000
Behind nginx:  app.<domain> → 127.0.0.1:8000, and Postmark's inbound webhook URL is
               https://<user>:<POSTMARK_INBOUND_WEBHOOK_SECRET>@app.<domain>/inbound/postmark
"""

from __future__ import annotations

import html
import logging
import threading

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import a2a, config, db, mail, postmark, receipts

log = logging.getLogger("concierge.webhook")


def _anchor_in_background(tenant_id, receipt) -> None:
    """Sign and anchor a receipt on X Layer without holding up the webhook response.

    Runs on its own thread, started after the reply has already been sent — the customer is
    never kept waiting on a mainnet confirmation. Deliberately not called from
    `mail.handle_inbound` itself: that function is shared with the verification harness, and a
    gate run must never have the side effect of spending real gas. Failure here leaves
    `signature`/`xlayer_tx` NULL, the honest state (see receipts.py) — logged, never papered
    over with a fabricated transaction hash.
    """
    try:
        with db.tenant_session(tenant_id) as cur:
            receipts.anchor(cur, receipt)
    except Exception:
        log.exception("Background anchor failed for receipt %s", receipt.receipt_id)

app = FastAPI(title="CONCIERGE inbound", docs_url=None, redoc_url=None)

OKX_REVIEW = {
    "reviewed_at": "2026-07-27T07:38:00Z",
    "agent": {
        "name": "CONCIERGE",
        "agent_id": "9274",
        "role": "ASP",
        "chain": "X Layer mainnet",
        "chain_id": 196,
        "service_id": "dea8f4fb-b2e7-4423-a6cd-b39aeb3ea027",
        "service_name": "Inbound enquiry handling",
        "service_type": "A2A",
    },
    "live_endpoints": {
        "review_page": "https://app.quietdesks.com/okx-review",
        "machine_readable": "https://app.quietdesks.com/okx-review.json",
        "liveness": "https://app.quietdesks.com/healthz",
        "readiness": "https://app.quietdesks.com/readyz",
        "receipt_example": (
            "https://app.quietdesks.com/r/ce573269-cf86-46b5-a682-6e614b48da47"
        ),
    },
    "completed_paid_test": {
        "job_id": "0x3646b7b21028eec33742c2dba81cc0d758597e674af7696773cc906f8282a608",
        "title": "CONCIERGE 30-day test",
        "buyer_agent_id": "9630",
        "provider_agent_id": "9274",
        "amount": "2.5 USDT",
        "final_status": "complete",
        "issued_inbox": "brightside-dental-2@inbox.quietdesks.com",
        "proved": [
            "provider application",
            "buyer acceptance and escrow funding",
            "unattended tenant onboarding over A2A",
            "delivery of a real dedicated inbox",
            "buyer review approval",
            "escrow release",
        ],
    },
    "verification": {
        "provisioning_suite": "16 passed, 0 failed, 1 declared transport stub",
        "production_readiness": "ready",
        "a2a_transport": "available",
        "a2a_daemon": "ready",
        "provider_authentication": "authenticated by live probe",
        "production_units": "7/7 active",
    },
    "reviewer_test": {
        "single_action": (
            "Create one private designated A2A job for Agent #9274 and service "
            "dea8f4fb-b2e7-4423-a6cd-b39aeb3ea027, asking CONCIERGE to set up inbound "
            "enquiry handling for the buyer's own service business."
        ),
        "expected": (
            "CONCIERGE responds in the job thread, validates the engagement price, and—after "
            "acceptance—conducts deterministic onboarding. Unknown prices or policies are "
            "escalated; they are never invented."
        ),
    },
    "marketplace_state": {
        "status": "not listed",
        "approval": "Listing under review",
        "approval_remark": "AI quality review timed out, automatically passed",
        "note": "All operator-controlled tests pass; public listing is awaiting OKX publication.",
    },
}


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """Process liveness only. Dependency readiness lives at /readyz."""
    return {
        "status": "ok",
        "sending_configured": config.postmark_token() is not None,
        "inbound_auth_configured": config.inbound_webhook_secret() is not None,
        "inbound_domain": config.inbound_domain(),
    }


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Operational readiness: fail when a core delivery dependency is unavailable."""
    checks = {
        "database": db.healthy(),
        "postmark_configured": config.postmark_token() is not None,
        "inbound_auth_configured": config.inbound_webhook_secret() is not None,
        "a2a_daemon": a2a.healthy(),
        "xlayer_configured": bool(config.xlayer_private_key() and config.xlayer_contract()),
    }
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.get("/okx-review.json")
def okx_review_json() -> dict[str, object]:
    """One reviewer-safe, machine-readable evidence bundle; contains no credentials."""
    return OKX_REVIEW


@app.get("/okx-review")
def okx_review_page() -> HTMLResponse:
    """A single human-readable handoff so marketplace review needs no repository setup."""
    e = html.escape
    agent = OKX_REVIEW["agent"]
    test = OKX_REVIEW["completed_paid_test"]
    endpoints = OKX_REVIEW["live_endpoints"]
    verification = OKX_REVIEW["verification"]
    market = OKX_REVIEW["marketplace_state"]
    proved = "".join(f"<li>{e(item)}</li>" for item in test["proved"])
    endpoint_rows = "".join(
        f'<li><a href="{e(url)}">{e(label.replace("_", " ").title())}</a></li>'
        for label, url in endpoints.items()
    )
    body = (
        "<h1>CONCIERGE — OKX review packet</h1>"
        "<p>Everything below is public, reviewer-safe evidence. No VPS access, credentials, "
        "database setup, or multi-suite test run is required.</p>"
        "<h2>Agent and service</h2>"
        f"<p><strong>{e(agent['name'])} #{e(agent['agent_id'])}</strong> · {e(agent['role'])} · "
        f"{e(agent['service_type'])}<br>Service: {e(agent['service_name'])}<br>"
        f"Service ID: <code>{e(agent['service_id'])}</code><br>"
        f"{e(agent['chain'])}, chain {agent['chain_id']}</p>"
        "<h2>One completed paid proof</h2>"
        f"<p>Job <code>{e(test['job_id'])}</code><br>{e(test['title'])} · "
        f"{e(test['amount'])} · final status: <strong>{e(test['final_status'])}</strong><br>"
        f"Delivered inbox: <code>{e(test['issued_inbox'])}</code></p>"
        f"<ul>{proved}</ul>"
        "<h2>Live links</h2>"
        f"<ul>{endpoint_rows}</ul>"
        "<p><code>/healthz</code> proves process/configuration liveness. <code>/readyz</code> "
        "returns HTTP 200 only when the database, email delivery, authenticated inbound path, "
        "A2A daemon, and X Layer configuration are ready.</p>"
        "<h2>Verification summary</h2>"
        f"<p>Provisioning: {e(verification['provisioning_suite'])}<br>"
        f"Production units: {e(verification['production_units'])}<br>"
        f"A2A: {e(verification['a2a_transport'])}; daemon {e(verification['a2a_daemon'])}; "
        f"provider {e(verification['provider_authentication'])}</p>"
        "<h2>Shortest reviewer test</h2>"
        f"<p>{e(OKX_REVIEW['reviewer_test']['single_action'])}</p>"
        f"<p><strong>Expected:</strong> {e(OKX_REVIEW['reviewer_test']['expected'])}</p>"
        "<h2>Safety properties to check</h2>"
        "<ul><li>Every outbound response discloses that it is an AI and offers a human route.</li>"
        "<li>Prices and floors come only from the onboarded business profile, never a model.</li>"
        "<li>An uncovered service, qualifier, policy, or suitability question escalates rather "
        "than receiving an invented answer.</li><li>Public receipt lookup exposes only a single "
        "eligible commitment by its unguessable receipt ID.</li></ul>"
        "<h2>Marketplace publication state</h2>"
        f"<p><strong>{e(market['status'])}</strong> · {e(market['approval'])}<br>"
        f"OKX remark: {e(market['approval_remark'])}<br>{e(market['note'])}</p>"
        f'<p style="color:#666;font-size:.9em">Evidence snapshot: '
        f"{e(OKX_REVIEW['reviewed_at'])}. Machine-readable copy: "
        f'<a href="/okx-review.json">/okx-review.json</a>.</p>'
    )
    return HTMLResponse(_PAGE.format(title="CONCIERGE — OKX review packet", body=body))


@app.post("/inbound/postmark")
async def inbound_postmark(request: Request) -> JSONResponse:
    secret = config.inbound_webhook_secret()
    if not mail.check_webhook_auth(request.headers.get("authorization"), secret):
        # 401, and deliberately terse: an unauthenticated caller learns nothing about us.
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request", "detail": "body was not JSON"},
                            status_code=400)

    token = config.postmark_token()
    if not token:
        # Approval/token not yet in place. Do not 5xx into a 3-day retry storm; report plainly.
        log.error("Inbound received but POSTMARK_SERVER_TOKEN is unset — cannot reply.")
        return JSONResponse(
            {"status": "accepted_but_cannot_reply",
             "detail": "POSTMARK_SERVER_TOKEN unset (OPERATOR_PROVIDES item 3)"},
            status_code=200)

    try:
        handled = mail.handle_inbound(payload, mailer=postmark.PostmarkMailer(token))
    except mail.Unroutable as e:
        # A message to an address no tenant owns. 200 so Postmark stops retrying; we log it.
        log.warning("Unroutable inbound: %s", e)
        return JSONResponse({"status": "unresolved_recipient"}, status_code=200)
    except postmark.PostmarkError as e:
        # The reply itself failed to send. This one *should* retry, so signal 5xx.
        log.error("Send failed: %s", e)
        return JSONResponse({"status": "send_failed", "detail": str(e)}, status_code=502)

    receipt = handled.outcome.receipt
    if receipt is not None and config.xlayer_private_key() and config.xlayer_contract():
        threading.Thread(
            target=_anchor_in_background, args=(handled.tenant_id, receipt), daemon=True,
        ).start()

    return JSONResponse(
        {"status": "ok", "state": handled.outcome.state_after,
         "action": handled.outcome.action, "replied": handled.reply_email is not None,
         "owner_alerted": handled.owner_alert_email is not None,
         "anchoring": receipt is not None and bool(config.xlayer_contract())},
        status_code=200)


# ---------------------------------------------------------------- public receipt verification
#
# Feature 3 (the public-receipt suite). Read-only, unauthenticated, scoped by receipt_id alone — see
# `db.get_public_receipt` and `receipts.public_view` for the isolation guarantee. No framework:
# this is plain server-rendered HTML, because a trust page has no business needing a build step.

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: -apple-system, sans-serif; max-width: 40em; margin: 4em auto; padding: 0 1em; color: #1a1a1a;">
{body}
</body></html>"""


def _not_found_page() -> HTMLResponse:
    """Identical for a malformed id, a nonexistent one, and one that exists but is not a public
    commitment (an internal-only receipt — a floor breach, an escalation, a queued draft). The
    caller cannot tell these apart, which is the point: a wrong or ineligible guess learns
    nothing about what CONCIERGE does or does not hold."""
    return HTMLResponse(_PAGE.format(
        title="Receipt not found",
        body=(
            "<h1>Receipt not found</h1>"
            "<p>There is no verifiable commitment at this address. If you followed a link from "
            "a CONCIERGE email, check that it was copied in full.</p>"
        ),
    ), status_code=404)


def _receipt_page(view: dict) -> HTMLResponse:
    e = html.escape
    if view["verified"]:
        integrity = ('<strong style="color:#127a3d;">Verified — unaltered since it was '
                     'written</strong>')
    else:
        integrity = ('<strong style="color:#b00020;">NOT VERIFIED — this record does not match '
                      'its own hash</strong>')

    if view["anchored"]:
        tx_url = config.xlayer_explorer_tx_url(view["xlayer_tx"])
        chain_line = (f'<p>On-chain: <a href="{e(tx_url)}">{e(view["xlayer_tx"])}</a> '
                      f'(X Layer mainnet, chain 196)</p>')
    else:
        chain_line = "<p>On-chain anchoring is queued but not yet confirmed.</p>"

    committed = (
        f'<pre style="white-space: pre-wrap; font-family: inherit; background: #f6f6f6; '
        f'padding: 1em; border-radius: 6px;">{e(view["committed_text"])}</pre>'
        if view["committed_text"] else
        "<p><em>This commitment has not been sent to the client yet.</em></p>"
    )

    body = (
        "<h1>Verified commitment</h1>"
        f"<p>{integrity}</p>"
        f"<p>Service: <strong>{e(view['service'] or '—')}</strong></p>"
        f"<p>Recorded: {e(str(view['created_at']))}</p>"
        f"{chain_line}"
        "<hr>"
        "<h2>What was committed</h2>"
        f"{committed}"
        "<hr>"
        '<p style="color:#666; font-size: 0.9em;">This offer was committed by an autonomous '
        "agent (CONCIERGE) and cannot be silently altered: the text above is hashed, and the "
        "hash is anchored on a public blockchain independent of CONCIERGE staying online. "
        f"Receipt id: {e(view['receipt_id'])}.</p>"
    )
    return HTMLResponse(_PAGE.format(title="Verified commitment", body=body))


@app.get("/r/{receipt_id}")
def verify_receipt(receipt_id: str) -> HTMLResponse:
    row = db.get_public_receipt(receipt_id)
    view = receipts.public_view(row)
    if view is None:
        return _not_found_page()
    return _receipt_page(view)
