"""The always-on inbound webhook (§12). This is the process systemd keeps alive on the VPS.

One job: receive Postmark's inbound POST, authenticate it, hand it to `concierge.mail`, and
answer 2xx so Postmark does not retry for three days. It holds no business logic — every
decision belongs to the engine, reached through `mail.handle_inbound`.

Run locally:   uvicorn concierge.app:app --host 0.0.0.0 --port 8000
Behind nginx:  app.<domain> → 127.0.0.1:8000, and Postmark's inbound webhook URL is
               https://<user>:<POSTMARK_INBOUND_WEBHOOK_SECRET>@app.<domain>/inbound/postmark
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config, mail, postmark

log = logging.getLogger("concierge.webhook")

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

    return JSONResponse(
        {"status": "ok", "state": handled.outcome.state_after,
         "action": handled.outcome.action, "replied": handled.reply_email is not None,
         "owner_alerted": handled.owner_alert_email is not None},
        status_code=200)
