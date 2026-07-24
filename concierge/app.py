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

from . import config, db, mail, postmark, receipts

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


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """Liveness for the systemd/nginx layer. Reports which credentials are actually present —
    truthfully, so 'up' never masks 'cannot send'."""
    return {
        "status": "ok",
        "sending_configured": config.postmark_token() is not None,
        "inbound_auth_configured": config.inbound_webhook_secret() is not None,
        "inbound_domain": config.inbound_domain(),
    }


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
# Feature 3 (GATE 6b). Read-only, unauthenticated, scoped by receipt_id alone — see
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
