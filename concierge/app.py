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
    """Guided 90-second product demo plus the full reviewer-safe evidence packet."""
    e = html.escape
    agent = OKX_REVIEW["agent"]
    test = OKX_REVIEW["completed_paid_test"]
    market = OKX_REVIEW["marketplace_state"]
    receipt_url = OKX_REVIEW["live_endpoints"]["receipt_example"]
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CONCIERGE — 90-second product demo</title>
<style>
:root{{--ink:#122018;--muted:#607066;--paper:#f3f1e8;--card:#fffef8;--green:#17653a;
--lime:#c9f45a;--line:#d8dacd;--warn:#b44a2a;--navy:#142f38}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 Inter,ui-sans-serif,
system-ui,-apple-system,sans-serif}} a{{color:var(--green)}} code{{font-family:ui-monospace,
SFMono-Regular,monospace;overflow-wrap:anywhere}} .wrap{{max-width:1180px;margin:auto;padding:28px}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}}
.brand{{font-weight:900;letter-spacing:.12em}} .pill{{border:1px solid var(--line);background:#fff;
border-radius:99px;padding:7px 12px;font-size:13px}} .live{{color:var(--green);font-weight:800}}
.live:before{{content:"";display:inline-block;width:8px;height:8px;background:#3bc96a;border-radius:50%;
margin-right:7px;box-shadow:0 0 0 4px #dff5e6}}
.hero{{background:var(--navy);color:white;border-radius:24px;padding:48px;display:grid;
grid-template-columns:1.3fr .7fr;gap:38px;min-height:460px;align-items:center}}
.eyebrow{{color:var(--lime);font-weight:800;text-transform:uppercase;letter-spacing:.09em;font-size:13px}}
h1{{font-size:clamp(42px,7vw,82px);line-height:.96;letter-spacing:-.055em;margin:14px 0 22px}}
h2{{font-size:clamp(29px,4vw,48px);line-height:1.02;letter-spacing:-.035em;margin:8px 0 18px}}
h3{{margin:0 0 8px;font-size:17px}} .lede{{font-size:21px;color:#dce9e1;max-width:680px}}
.hero-card{{background:#fff;color:var(--ink);padding:24px;border-radius:18px}}
.metric{{font-size:38px;font-weight:900;line-height:1}} .small{{font-size:13px;color:var(--muted)}}
.button{{appearance:none;border:0;border-radius:12px;background:var(--lime);color:#132018;font-weight:900;
padding:14px 18px;cursor:pointer;font-size:15px}} .button.secondary{{background:#fff;border:1px solid var(--line)}}
.controls{{position:sticky;top:12px;z-index:10;margin:18px 0;background:rgba(255,254,248,.96);
backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:16px;padding:12px 15px;
display:flex;align-items:center;gap:12px;box-shadow:0 8px 30px #18201918}}
.track{{height:7px;background:#e1e3d8;border-radius:9px;flex:1;overflow:hidden}}
.bar{{height:100%;width:0;background:var(--green);transition:width .25s}} #clock{{font-weight:900;
min-width:48px;text-align:right}} .scene{{scroll-margin-top:90px;min-height:560px;padding:52px 0;
border-bottom:1px solid var(--line);display:grid;align-content:center}} .kicker{{font-weight:900;
color:var(--green);font-size:13px;text-transform:uppercase;letter-spacing:.09em}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}
.grid.three{{grid-template-columns:repeat(3,minmax(0,1fr))}} .card{{background:var(--card);
border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:0 5px 18px #1820190a}}
.card.accent{{background:#e9f6b9;border-color:#b8db51}} .card.dark{{background:var(--navy);color:white}}
.label{{text-transform:uppercase;letter-spacing:.08em;font-size:11px;color:var(--muted);font-weight:900}}
.value{{font-size:26px;font-weight:900;margin-top:5px}} .flow{{display:flex;align-items:center;
justify-content:space-between;gap:8px;margin-top:25px}} .step{{flex:1;text-align:center;padding:16px 8px;
border:1px solid var(--line);border-radius:14px;background:#fff}} .arrow{{color:var(--muted)}}
.rules{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}} .rule{{padding:18px;
background:#fff;border:1px solid var(--line);border-radius:14px}} .rule b{{font-size:21px}}
.mail{{border:1px solid var(--line);border-radius:16px;background:white;overflow:hidden}}
.mailhead{{background:#edf0e8;border-bottom:1px solid var(--line);padding:12px 18px;font-size:13px}}
.mailbody{{padding:22px;font-size:17px}} .ai{{border-left:5px solid var(--lime)}} .safe{{border-left:5px solid #ef8d68}}
.decision{{display:inline-block;padding:6px 10px;border-radius:7px;background:#dcf3df;color:#145b32;
font-weight:900;font-size:12px}} .decision.warn{{background:#fbe4da;color:#8d321d}}
.quote{{font-size:23px;line-height:1.35}} .proof{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.check{{display:flex;gap:10px;margin:9px 0}} .check:before{{content:"✓";color:var(--green);font-weight:900}}
.narrator{{margin-top:20px;background:#15251d;color:#fff;border-radius:14px;padding:16px 19px}}
.narrator b{{color:var(--lime)}} footer{{padding:45px 0;color:var(--muted);font-size:13px}}
@media(max-width:760px){{.hero,.grid,.grid.three,.proof{{grid-template-columns:1fr}}.hero{{padding:28px}}
.rules{{grid-template-columns:1fr 1fr}}.flow{{display:grid;grid-template-columns:1fr}}.arrow{{display:none}}
.wrap{{padding:16px}}.controls{{top:5px}}}}
</style></head><body><main class="wrap">
<div class="top"><div class="brand">CONCIERGE</div><div class="pill live" id="liveState">checking production</div></div>

<section class="hero scene" id="scene-0" data-seconds="10">
 <div><div class="eyebrow">OKX A2A autonomous revenue agent</div>
 <h1>Your inbound,<br>closed while<br>you’re away.</h1>
 <p class="lede">CONCIERGE qualifies, quotes, negotiates and books for service businesses—using
 their rules, never invented ones.</p>
 <button class="button" onclick="startDemo()">▶ Start guided 90-second demo</button></div>
 <div class="hero-card"><div class="label">Live ASP</div><div class="metric">#{e(agent['agent_id'])}</div>
 <p><b>{e(agent['service_name'])}</b><br>{e(agent['service_type'])} · X Layer mainnet</p>
 <hr style="border:0;border-top:1px solid #ddd"><p class="small">Service ID</p>
 <code>{e(agent['service_id'])}</code></div>
</section>

<div class="controls"><button class="button secondary" onclick="prevScene()">←</button>
<button class="button" id="play" onclick="toggleDemo()">▶ 90s demo</button>
<div class="track"><div class="bar" id="bar"></div></div><span id="clock">0:00</span>
<button class="button secondary" onclick="nextScene()">→</button></div>

<section class="scene" id="scene-1" data-seconds="15"><div class="kicker">1 · Commerce proof</div>
<h2>A real agent bought it. Escrow settled.</h2>
<div class="grid three"><div class="card"><div class="label">OKX job</div>
<div class="value">Complete</div><code>{e(test['job_id'])}</code></div>
<div class="card accent"><div class="label">Paid through escrow</div><div class="value">{e(test['amount'])}</div>
<p>Buyer #{e(test['buyer_agent_id'])} → ASP #{e(test['provider_agent_id'])}</p></div>
<div class="card"><div class="label">Delivered product</div><div class="value">Live inbox</div>
<code>{e(test['issued_inbox'])}</code></div></div>
<div class="flow"><div class="step">Applied</div><div class="arrow">→</div><div class="step">Funded</div>
<div class="arrow">→</div><div class="step">Onboarded</div><div class="arrow">→</div>
<div class="step">Delivered</div><div class="arrow">→</div><div class="step">Released</div></div>
<div class="narrator"><b>Say:</b> “This is not a mocked checkout. Agent 9630 hired CONCIERGE,
funded 2.5 USDT, completed unattended onboarding, received a live inbox, approved delivery,
and released escrow.”</div></section>

<section class="scene" id="scene-2" data-seconds="15"><div class="kicker">2 · Deterministic onboarding</div>
<h2>The model understands words.<br>The owner supplies every fact.</h2>
<div class="rules"><div class="rule"><span class="label">Examination</span><br><b>60 USDT</b><br>30 min</div>
<div class="rule"><span class="label">Hygiene</span><br><b>90 USDT</b><br>45 min</div>
<div class="rule"><span class="label">Hard floor</span><br><b>55 USDT</b><br>never crossed</div>
<div class="rule"><span class="label">Autonomy</span><br><b>8% max</b><br>owner-defined</div></div>
<div class="grid" style="margin-top:18px"><div class="card"><h3>Stored business rules</h3>
<div class="check">Weekdays, 09:00–17:00 Europe/London</div><div class="check">One patient per appointment</div>
<div class="check">Clinic only; no travel</div></div><div class="card"><h3>Always human</h3>
<div class="check">Clinical advice and emergencies</div><div class="check">Complaints and refunds</div>
<div class="check">Anything outside stored services</div></div></div>
<div class="narrator"><b>Say:</b> “The buyer’s own answers become executable rules. Prices,
floor, availability and escalation policy cannot come from the LLM or a template example.”</div></section>

<section class="scene" id="scene-3" data-seconds="15"><div class="kicker">3 · Correct autonomous answer</div>
<h2>Fast when the profile covers the question.</h2><div class="grid">
<div class="mail"><div class="mailhead">Inbound · patient@example.com</div><div class="mailbody">
<span class="decision">ANSWERABLE</span><p class="quote">“How much is a dental examination,
and how long does it take?”</p></div></div>
<div class="mail ai"><div class="mailhead">CONCIERGE · deterministic replay</div><div class="mailbody">
<p><b>This is an AI agent, not a person. Reply here if you would prefer a human.</b></p>
<p>A dental examination is <b>60 USDT</b> and takes <b>30 minutes</b>.</p>
<p>Would you like me to find an available appointment?</p></div></div></div>
<div class="narrator"><b>Say:</b> “For an answerable enquiry, it responds immediately with the
stored price and duration, discloses it is AI in line one, and moves the customer toward booking.”</div></section>

<section class="scene" id="scene-4" data-seconds="15"><div class="kicker">4 · The safety differentiator</div>
<h2>Uncertainty reduces autonomy.<br>It never creates permission.</h2><div class="grid">
<div class="mail"><div class="mailhead">Inbound · patient@example.com</div><div class="mailbody">
<span class="decision warn">CLINICAL / UNCOVERED</span><p class="quote">“I have severe swelling.
Do I need emergency treatment—and can you do it for 40 USDT?”</p></div></div>
<div class="mail safe"><div class="mailhead">CONCIERGE · guardrail result</div><div class="mailbody">
<p><b>Escalated to a human.</b></p><p>No medical advice. No 40-USDT promise. No disclosure of the
private 55-USDT floor.</p><p>The owner receives the customer’s actual words and takes over.</p></div></div></div>
<div class="narrator"><b>Say:</b> “This is the product’s core. Clinical judgment escalates,
and the requested price is below the stored floor. CONCIERGE neither advises nor bargains past
authority—even if a model sounds confident.”</div></section>

<section class="scene" id="scene-5" data-seconds="12"><div class="kicker">5 · Verifiable commitments</div>
<h2>A promise the agent cannot rewrite later.</h2><div class="proof">
<a class="card accent" href="{e(receipt_url)}" target="_blank"><div class="label">Public receipt</div>
<div class="value">Verified · unaltered</div><p>Open the exact customer-safe commitment →</p></a>
<div class="card dark"><div class="label" style="color:#a8bdb1">Independent settlement layer</div>
<div class="value">X Layer mainnet</div><p>Committed text is hashed and anchored on chain 196.</p>
<code>0x770cc76e…34bd667</code></div></div>
<div class="narrator"><b>Say:</b> “Every quote, counter and booking can carry a public receipt.
The exact commitment is hashed and anchored on X Layer, so it cannot be silently changed later.”</div></section>

<section class="scene" id="scene-6" data-seconds="8"><div class="kicker">6 · Live production</div>
<h2>One product. Three interfaces.</h2><div class="grid three"><div class="card"><div class="value">OKX A2A</div>
<p>Purchase, escrow and autonomous onboarding.</p></div><div class="card"><div class="value">Email</div>
<p>The interface customers and owners already use.</p></div><div class="card"><div class="value">Public proof</div>
<p>Independent receipt verification without login.</p></div></div>
<div class="card" style="margin-top:18px"><span class="live">Production ready</span>
<span id="checks" class="small"> · loading dependency checks…</span></div>
<div class="narrator"><b>Close:</b> “CONCIERGE turns an unattended inbox into safely closed
revenue: fast enough to act alone, constrained enough to trust, and independently verifiable.”</div></section>

<footer><b>Reviewer evidence:</b> <a href="/okx-review.json">machine-readable packet</a> ·
<a href="/readyz">live readiness</a> · <a href="{e(receipt_url)}">public receipt</a><br>
Agent #{e(agent['agent_id'])} is {e(market['approval'])}; completed private A2A commerce is proven above.
</footer></main>
<script>
const scenes=[...document.querySelectorAll('.scene')], durations=scenes.map(x=>+x.dataset.seconds);
const total=durations.reduce((a,b)=>a+b,0); let index=0, elapsed=0, timer=null;
function show(i){{index=Math.max(0,Math.min(i,scenes.length-1));scenes[index].scrollIntoView({{behavior:'smooth'}});
 elapsed=durations.slice(0,index).reduce((a,b)=>a+b,0);paint()}}
function paint(){{document.getElementById('bar').style.width=(elapsed/total*100)+'%';
 document.getElementById('clock').textContent=Math.floor(elapsed/60)+':'+String(elapsed%60).padStart(2,'0')}}
function startDemo(){{show(0);if(!timer)toggleDemo()}} function nextScene(){{show(index+1)}} function prevScene(){{show(index-1)}}
function toggleDemo(){{const btn=document.getElementById('play');if(timer){{clearInterval(timer);timer=null;
 btn.textContent='▶ Resume'}}else{{btn.textContent='Ⅱ Pause';timer=setInterval(()=>{{elapsed++;paint();
 let boundary=durations.slice(0,index+1).reduce((a,b)=>a+b,0);if(elapsed>=boundary&&index<scenes.length-1)
 {{index++;scenes[index].scrollIntoView({{behavior:'smooth'}})}}if(elapsed>=total){{clearInterval(timer);
 timer=null;btn.textContent='↻ Replay'}}}},1000)}}}}
fetch('/readyz').then(r=>r.json()).then(d=>{{const ok=d.status==='ready';document.getElementById('liveState').textContent=
 ok?'live production · ready':'production check failed';document.getElementById('checks').textContent=' · '+
 Object.entries(d.checks).map(([k,v])=>k.replaceAll('_',' ')+': '+(v?'ready':'fail')).join(' · ')}})
.catch(()=>document.getElementById('liveState').textContent='production status unavailable');paint();
</script></body></html>"""
    return HTMLResponse(page)


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
