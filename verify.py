#!/usr/bin/env python3
"""CONCIERGE verification harness.

Runs real logic against real inputs and prints a plain-English report a non-engineer can read.
A bare green check is forbidden: every PASS must be accompanied by the raw evidence that earned it.

    python verify.py --phase 0

Phase 0 makes live network calls. It has no mocks and no fixtures. If the network is down, it
reports FAIL — it does not fall back to a cached answer and call that a pass.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 25

# Cal.com sits behind Cloudflare, which rejects urllib's default agent with error 1010.
# A real client identifies itself; this is not a workaround, it is correct HTTP citizenship.
USER_AGENT = "CONCIERGE-verify/0.1 (+https://github.com/concierge-asp)"


# ---------------------------------------------------------------- plumbing

class Report:
    """Collects checks and prints them as prose with evidence attached."""

    def __init__(self, phase: str, title: str, preamble: str = ""):
        self.phase = phase
        self.title = title
        self.preamble = preamble
        self.checks: list[tuple[str, bool, str, str]] = []

    def check(self, name: str, passed: bool, finding: str, evidence: str = "") -> bool:
        self.checks.append((name, passed, finding, evidence.strip()))
        return passed

    def note(self, name: str, finding: str, evidence: str = "") -> None:
        """A recorded observation that is not pass/fail — e.g. a missing credential."""
        self.checks.append((name, None, finding, evidence.strip()))

    def render(self) -> int:
        line = "=" * 78
        print(line)
        print(f"CONCIERGE · VERIFY · PHASE {self.phase} — {self.title}")
        print(line)
        if self.preamble:
            for para in self.preamble.strip().split("\n"):
                print(para)

        for name, passed, finding, evidence in self.checks:
            mark = {True: "PASS", False: "FAIL", None: "INFO"}[passed]
            print(f"\n[{mark}] {name}")
            for para in finding.strip().split("\n"):
                print(f"       {para}")
            if evidence:
                print("       ---- raw evidence ----")
                for ln in evidence.split("\n"):
                    print(f"       | {ln}")

        failed = [n for n, p, _, _ in self.checks if p is False]
        passed_n = sum(1 for _, p, _, _ in self.checks if p is True)
        info_n = sum(1 for _, p, _, _ in self.checks if p is None)

        print("\n" + line)
        print(f"RESULT: {passed_n} passed, {len(failed)} failed, {info_n} informational")
        if failed:
            print("FAILED CHECKS: " + ", ".join(failed))
            print(f"PHASE {self.phase} VERDICT: FAIL — do not proceed past GATE {self.phase}.")
        else:
            print(f"PHASE {self.phase} VERDICT: PASS")
        print(line)
        return 1 if failed else 0


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None):
    """Returns (status, text). Network failures raise — we never swallow them into a pass."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, method=method, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def rpc(url: str, method: str):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": [], "id": 1}).encode()
    status, text = http("POST", url, {"Content-Type": "application/json"}, payload)
    return status, text


def clip(s: str, n: int = 260) -> str:
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + " …"


# ---------------------------------------------------------------- phase 0

# Pinned per the Verification Ledger. These differ per endpoint on purpose — see ledger §1 and §2.
CAL_BOOKINGS_VERSION = "2026-02-25"
CAL_SLOTS_VERSION = "2024-09-04"
XLAYER_MAINNET_RPC = "https://rpc.xlayer.tech"
XLAYER_TESTNET_RPC = "https://testrpc.xlayer.tech"
XLAYER_MAINNET_CHAIN_ID = 196
XLAYER_TESTNET_CHAIN_ID = 1952

OPERATOR_ITEMS = [
    ("VPS_HOST", "1. VPS (24/7 host)", "Phase 4 inbound webhook, Phase 7 workers"),
    ("CONCIERGE_DOMAIN", "2. Domain for inbox.<domain>", "Phase 4 email"),
    ("POSTMARK_SERVER_TOKEN", "3. Postmark server API token", "Phase 4 email"),
    ("CAL_API_KEY", "4. Cal.com API key", "Phase 5 real bookings"),
    ("OKX_API_KEY", "5. OKX Agentic Wallet credentials", "Phase 7 escrow + settlement"),
    ("XLAYER_PRIVATE_KEY", "6. Funded OKB signer on X Layer MAINNET (196)",
     "Phase 6 receipt anchoring, Phase 7 settlement"),
    ("LLM_API_KEY", "7. LLM API key", "Phase 2 classification, Phase 3 drafting"),
    ("SEARCH_API_KEY", "8. Web-search key (OPTIONAL)", "Phase 2 vertical enrichment"),
]


def phase0() -> int:
    r = Report(0, "Foundations & external verification")

    # --- Cal.com slots: a real 200 with real data, no key needed on public event types.
    try:
        url = ("https://api.cal.com/v2/slots"
               "?eventTypeId=1&start=2026-07-23&end=2026-07-30")
        status, text = http("GET", url, {"cal-api-version": CAL_SLOTS_VERSION})
        ok = status == 200 and '"data"' in text
        has_utc = "Z\"" in text or "Z'" in text
        r.check(
            "Cal.com slots endpoint is live and speaks the version we pinned",
            ok,
            f"Asked Cal.com for open slots with header cal-api-version: {CAL_SLOTS_VERSION}.\n"
            + (
                f"It answered HTTP {status} with real availability. Times come back in UTC with a "
                f"trailing Z ({'confirmed' if has_utc else 'NOT SEEN — investigate'}), which is "
                f"the format our booking code must send back."
                if ok else
                f"It answered HTTP {status} and did NOT return availability. We have no proof the "
                f"slots contract is what we think it is, so Phase 5 must not be built yet."
            ),
            f"GET {url}\ncal-api-version: {CAL_SLOTS_VERSION}\nHTTP {status}\n{clip(text)}",
        )
    except Exception as e:
        r.check("Cal.com slots endpoint is live", False,
                f"Could not reach Cal.com slots at all: {e}")

    # --- Cal.com bookings: prove the pinned version, and prove a stale pin silently downgrades.
    try:
        hdr = {"cal-api-version": CAL_BOOKINGS_VERSION, "Content-Type": "application/json"}
        status_new, text_new = http("POST", "https://api.cal.com/v2/bookings", hdr, b"{}")
        wants_iso = "must be a valid ISO 8601" in text_new
        wants_attendee = "attendee" in text_new
        r.check(
            "Cal.com bookings enforces the contract we designed against",
            wants_iso and wants_attendee,
            f"Sent a deliberately empty booking to Cal.com with header "
            f"cal-api-version: {CAL_BOOKINGS_VERSION}. It rejected the request and told us exactly "
            f"what it wants: a start time as a valid ISO 8601 date "
            f"({'confirmed' if wants_iso else 'NOT CONFIRMED'}) and a nested attendee object "
            f"({'confirmed' if wants_attendee else 'NOT CONFIRMED'}). This is the real server "
            f"stating its own rules — not us assuming them.",
            f"POST /v2/bookings  cal-api-version: {CAL_BOOKINGS_VERSION}  body: {{}}\n"
            f"HTTP {status_new}\n{clip(text_new, 320)}",
        )

        hdr_old = {"cal-api-version": "1999-01-01", "Content-Type": "application/json"}
        _, text_old = http("POST", "https://api.cal.com/v2/bookings", hdr_old, b"{}")
        downgraded = ("must be a string" in text_old) and not ("ISO 8601" in text_old)
        r.check(
            "A stale version pin silently downgrades instead of erroring (danger confirmed)",
            downgraded,
            "Sent the exact same empty booking with a nonsense version header. Cal.com did NOT\n"
            "reject the bad version — it quietly fell back to an older contract with different\n"
            "validation rules ('start must be a string' instead of 'must be a valid ISO 8601').\n"
            "This is why the build spec's suggested 2024-08-13 pin was dangerous: we'd have been\n"
            "silently validated by the wrong schema. The harness now guards the pin.",
            f"POST /v2/bookings  cal-api-version: 1999-01-01  body: {{}}\n{clip(text_old, 320)}",
        )
    except Exception as e:
        r.check("Cal.com bookings contract", False, f"Could not reach Cal.com bookings: {e}")

    # --- X Layer: live chain, both networks.
    for label, url, expected in (
        ("mainnet", XLAYER_MAINNET_RPC, XLAYER_MAINNET_CHAIN_ID),
        ("testnet", XLAYER_TESTNET_RPC, XLAYER_TESTNET_CHAIN_ID),
    ):
        try:
            status, text = rpc(url, "eth_chainId")
            got = json.loads(text).get("result")
            got_int = int(got, 16) if got else None
            r.check(
                f"X Layer {label} is live and is chain {expected}",
                got_int == expected,
                f"Asked the X Layer {label} node what chain it is. It answered {got} = "
                f"{got_int}, and we required {expected}. "
                + ("MAINNET IS WHERE RECEIPTS ARE ANCHORED — anchoring to the wrong chain would "
                   "make every proof worthless, so the chain id is asserted, never assumed."
                   if label == "mainnet" else
                   "Testnet is checked for liveness only. It is NEVER a source of evidence for a "
                   "gate: a receipt anchored on a testnet proves nothing to a customer or an "
                   "arbitrator. See ledger §9."),
                f"POST {url}  eth_chainId\nHTTP {status}\n{clip(text)}",
            )
        except Exception as e:
            r.check(f"X Layer {label} reachable", False, f"Could not reach {url}: {e}")

    try:
        _, text = rpc(XLAYER_MAINNET_RPC, "eth_blockNumber")
        blk = int(json.loads(text)["result"], 16)
        r.check(
            "X Layer mainnet is actually producing blocks",
            blk > 0,
            f"Current block height is {blk:,}. A chain ID alone proves nothing if the chain is "
            f"stalled; this proves it is alive and will accept our receipt transactions.",
            f"eth_blockNumber → {clip(text)}  (decimal {blk:,})",
        )
    except Exception as e:
        r.check("X Layer mainnet producing blocks", False, f"Block height query failed: {e}")

    # --- Mainnet is affordable. Measured, so that "we anchor for real" is never argued on cost.
    try:
        _, gtext = rpc(XLAYER_MAINNET_RPC, "eth_gasPrice")
        gas_price = int(json.loads(gtext)["result"], 16)
        _, ptext = http("GET", "https://www.okx.com/api/v5/market/ticker?instId=OKB-USDT")
        okb_usd = float(json.loads(ptext)["data"][0]["last"])
        per_receipt_okb = gas_price * 55_000 / 1e18
        per_receipt_usd = per_receipt_okb * okb_usd
        anchors_per_okb = 1 / per_receipt_okb
        r.check(
            "Anchoring on MAINNET is affordable — so cost is never a reason to fake it on testnet",
            per_receipt_usd < 0.01,
            f"X Layer mainnet gas price is {gas_price / 1e9:.4f} gwei and OKB is ${okb_usd:,.2f}. "
            f"A receipt anchor (~55,000 gas) therefore costs about ${per_receipt_usd:.6f} — roughly "
            f"one hundredth of a cent. One OKB buys about {anchors_per_okb:,.0f} anchors.\n"
            f"This check exists to close off an argument, not to open one: there is no cost case "
            f"for proving receipts on a testnet. A receipt anchored on a testnet is worth nothing "
            f"to a customer disputing a quote or an arbitrator ruling on an escrow, and the receipt "
            f"IS the product's trust claim. CONCIERGE anchors on chain 196. See ledger §9.",
            f"eth_gasPrice → {gas_price:,} wei ({gas_price / 1e9:.9f} gwei)\n"
            f"OKB-USDT last → ${okb_usd:,.2f}\n"
            f"55,000 gas = {per_receipt_okb:.8f} OKB = ${per_receipt_usd:.6f}\n"
            f"1 OKB = {anchors_per_okb:,.0f} receipt anchors",
        )
    except Exception as e:
        r.check("Mainnet anchoring cost measured", False,
                f"Could not measure live gas/OKB price: {e}")

    # --- Docs that must exist and be non-trivial.
    for path, label in (
        ("docs/VERIFICATION_LEDGER.md", "Verification Ledger"),
        ("docs/OPERATOR_PROVIDES.md", "Operator-provides register"),
    ):
        exists = os.path.isfile(path) and os.path.getsize(path) > 1000
        r.check(
            f"{label} exists and is filled in",
            exists,
            f"{path} is {'present with real content' if exists else 'MISSING or near-empty'}. "
            f"GATE 0 cannot pass without it.",
            f"{path}: {os.path.getsize(path) if os.path.isfile(path) else 0} bytes",
        )

    # --- Operator credentials: report honestly, never fabricate.
    missing = []
    for env, label, blocks in OPERATOR_ITEMS:
        if os.environ.get(env):
            r.note(f"Credential present — {label}", f"Found in environment. Unblocks: {blocks}.")
        else:
            missing.append(label)
    if missing:
        r.note(
            "Operator credentials still missing",
            "None of these are faked or stubbed anywhere in the codebase. The phases they gate "
            "are simply not started:\n" + "\n".join(f"  - {m}" for m in missing) +
            "\nPhases 1, 2 and 3 need none of them and can proceed now. Phase 6 can be written but "
            "not proven:\nreceipts anchor on X Layer MAINNET (196), which needs item 6. We do not "
            "anchor to a testnet and\ncall it evidence — see ledger §9.",
        )

    # --- The deadline, stated plainly every run.
    r.note(
        "Submission deadline (verified live 2026-07-22)",
        "OKX.AI Genesis Hackathon submissions close 2026-07-27 22:59 UTC. No extension listed.\n"
        "Note also: there is no 'Business Potential' track — the closest fits are Revenue Rocket\n"
        "and Best Product. See docs/VERIFICATION_LEDGER.md §7.",
    )

    return r.render()


# ---------------------------------------------------------------- phase 1

def phase1() -> int:
    r = Report(
        1,
        "Tenant model & isolation",
        preamble=(
            "\nGATE 1 asks one question: can tenant B reach tenant A's data? The answer below is\n"
            "not an assertion, it is eight attacks. Each one is a real query run as the real\n"
            "application role against a real PostgreSQL server, with its real result pasted in.\n"
            "The happy path is one check out of ten; the other nine are attempts to break it.\n"
        ),
    )
    try:
        from concierge import verify_phase1
    except ImportError as e:
        r.check("Phase 1 code is importable", False,
                f"Could not import the phase 1 module: {e}\n"
                f"Install the driver with: pip install 'psycopg[binary]'")
        return r.render()

    try:
        verify_phase1.run(r)
    except Exception as e:
        import traceback
        r.check(
            "Phase 1 harness completed", False,
            f"The harness itself raised {type(e).__name__}: {e}\n"
            f"This is reported as a FAIL rather than swallowed. Most likely cause: no PostgreSQL\n"
            f"at the configured DATABASE_URL. Start one with:\n"
            f"  docker run -d --name concierge-pg -e POSTGRES_PASSWORD=concierge \\\n"
            f"    -e POSTGRES_USER=concierge -e POSTGRES_DB=concierge -p 5432:5432 postgres:16-alpine",
            traceback.format_exc(),
        )
    return r.render()


# ---------------------------------------------------------------- phase 2

def phase2() -> int:
    r = Report(
        2,
        "Vertical-aware onboarding",
        preamble=(
            "\nGATE 2 runs three real business descriptions — an estate agency, a barrister and a\n"
            "spa — through onboarding. Checks 1-7 are the gate's own requirements. Checks 8-11 are\n"
            "the attacks, and they are the ones worth reading: the expensive failure in this phase\n"
            "is not a wrong template, it is a template's EXAMPLE price silently becoming a real\n"
            "business's real price.\n"
        ),
    )
    try:
        from concierge import verify_phase2
        verify_phase2.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 2 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


# ---------------------------------------------------------------- phase 3

def phase3() -> int:
    r = Report(
        3,
        "State machine & deterministic guardrails",
        preamble=(
            "\nGATE 3 is the decisive one, and it needs no credentials and no network. A real\n"
            "conversation runs end to end against a real PostgreSQL database: quoted from the\n"
            "profile, negotiated against the floor, booked. Checks 1-5 are the gate's own\n"
            "requirements; 6-16 are the attacks.\n"
            "One fixture is used — the calendar — and it is declared as such in every check that\n"
            "touches it. The production default refuses to book at all, which check 12 proves.\n"
        ),
    )
    try:
        from concierge import verify_phase3
        verify_phase3.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 3 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase4() -> int:
    r = Report(
        4,
        "Email connector (Postmark)",
        preamble=(
            "\nGATE 4 turns a real email into a state-machine step and a reply. Checks 1-2 are the\n"
            "gate's own path: a real Postmark inbound document is parsed, routed to the one tenant\n"
            "that owns the address, quoted from that tenant's profile, and answered FROM the\n"
            "tenant's own inbox. Checks 3-8 are the attacks — an orphan recipient, a +tag leak\n"
            "attempt, an unauthenticated webhook, and the threading that keeps a conversation\n"
            "together.\n"
            "One stand-in is used — a recording mailer — declared as a fixture in every check that\n"
            "touches it, exactly as GATE 3 declared its calendar. Live inbox delivery needs\n"
            "operator items 1-3 and is reported honestly as pending, never as a pass.\n"
        ),
    )
    try:
        from concierge import verify_phase4
        verify_phase4.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 4 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase5() -> int:
    r = Report(
        5,
        "Booking (live Cal.com)",
        preamble=(
            "\nGATE 5 replaces GATE 3's fixture calendar with real Cal.com v2 calls. It fetches\n"
            "real availability, runs the full NEW -> BOOKED journey, creates a real booking with a\n"
            "UTC start and nested attendee, confirms it by the API's own status, and then cancels\n"
            "it so a real calendar is not left with clutter. The one added object wraps the real\n"
            "adapter to observe it; every network call it makes is live.\n"
        ),
    )
    try:
        from concierge import verify_phase5
        verify_phase5.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 5 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase6() -> int:
    r = Report(
        6,
        "Receipts anchored on X Layer mainnet (196)",
        preamble=(
            "\nGATE 6 anchors real receipts — the same `Outcome.receipt` a real conversation\n"
            "writes in production — on X Layer mainnet. Every check below makes a live RPC call;\n"
            "nothing is cached from an earlier deploy or a previous run. Two independent proofs\n"
            "are checked: an offline signature over the content hash, and an on-chain transaction\n"
            "anchoring the same hash in the deployed ReceiptAnchor contract.\n"
        ),
    )
    try:
        from concierge import verify_phase6
        verify_phase6.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 6 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase3b2() -> int:
    r = Report(
        "3b-2",
        "Confidence-scored autonomy (Feature 2)",
        preamble=(
            "\nGATE 3b-2 extends GATE 3's state machine with one more question per pricing\n"
            "decision: not just 'is this within the rules', but 'how confident is CONCIERGE that\n"
            "it should send this without a human looking first'. The score is arithmetic over\n"
            "three named, stored signals (concierge/confidence.py) — never an LLM's self-reported\n"
            "certainty, and it never changes a price. Checks 1-2 are a thin profile queuing and a\n"
            "complete one auto-sending; 3-4 prove the score is persisted and retrievable, not just\n"
            "shown once; 5 is the regression proof that GATE 3's baseline is unaffected.\n"
        ),
    )
    try:
        from concierge import verify_phase3b
        verify_phase3b.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 3b-2 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase3b3() -> int:
    r = Report(
        "3b-3",
        "The decaying floor (Feature 5)",
        preamble=(
            "\nGATE 3b-3 extends GATE 3's guardrail check with an OPTIONAL, richer floor shape:\n"
            "instead of one static number, a tenant may set a curve that decays from an initial\n"
            "point down to an absolute floor as a negotiation goes on. Checks 1-2 walk a real,\n"
            "five-round negotiation down the curve; check 3 is the red-team — six real rounds\n"
            "past where the curve runs out, proving the absolute floor never breaks; check 4 is\n"
            "the regression proof that a tenant with no curve set is untouched by any of it.\n"
        ),
    )
    try:
        from concierge import verify_phase3b3
        verify_phase3b3.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 3b-3 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase3b4() -> int:
    r = Report(
        "3b-4",
        "Safe Follow-Up",
        preamble=(
            "\nGATE 3b-4 proves the last piece of the Phase-3 family: a thread that already has a\n"
            "real prospect on it gets nudged once, referencing what was actually quoted, after it\n"
            "goes quiet — and stays DEAD, not re-nudged forever, if there's still no reply. Check\n"
            "3 is the one to read closely: it is the negative test proving this cannot become\n"
            "cold outbound, whatever the clock says.\n"
        ),
    )
    try:
        from concierge import verify_phase3b4
        verify_phase3b4.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 3b-4 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase6b() -> int:
    r = Report(
        "6b",
        "Public receipt verification (Feature 3)",
        preamble=(
            "\nGATE 6b anchors real receipts on X Layer mainnet — same call, same real (tiny) gas\n"
            "as GATE 6 — and proves the public, unauthenticated `/r/{receipt_id}` page reads them\n"
            "back correctly. Checks 3-5 are the attacks: a nonexistent id, a malformed id, and an\n"
            "internal-only receipt (a real, anchored floor breach) all render the identical clean\n"
            "'not found' page — never distinguishable, never a leak.\n"
        ),
    )
    try:
        from concierge import verify_phase6b
        verify_phase6b.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 6b harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase8() -> int:
    r = Report(
        "8",
        "Tenant summary + scheduled actions",
        preamble=(
            "\nGATE 8 proves the summary's numbers are counted from real conversations this gate\n"
            "runs, not asserted, and that the scheduler's three jobs (receipt anchoring,\n"
            "follow-up dispatch, periodic summary) all read and write the same real rows. Check 5\n"
            "deliberately does not spend any NEW real mainnet gas — GATE 6 and GATE 6b already\n"
            "prove the anchoring mechanism itself, repeatedly, so this gate only proves the\n"
            "scheduled job's honest no-credentials skip.\n"
        ),
    )
    try:
        from concierge import verify_phase8
        verify_phase8.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 8 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase8b1() -> int:
    r = Report(
        "8b-1",
        "Product-gap intelligence (Feature 1)",
        preamble=(
            "\nGATE 8b-1 turns Phase 3's existing 'asked for something not in the profile ->\n"
            "ESCALATE, never invent' transition into a market signal. Check 1 proves the one new\n"
            "side effect (a verbatim GapEvent row); check 2 is the payoff (it surfaces in the\n"
            "owner summary, word for word); check 3 proves a floor breach is NOT a gap; check 4 is\n"
            "the isolation attack (one tenant's gaps never reach another's report); check 5 proves\n"
            "honest degradation with no LLM key. No new isolation mechanism — gap_events carries\n"
            "the same RLS policy GATE 1 already proved.\n"
        ),
    )
    try:
        from concierge import verify_phase8b1
        verify_phase8b1.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 8b-1 harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


def phase3c() -> int:
    r = Report(
        "3c",
        "Comprehension — answering the question actually asked",
        preamble=(
            "\nEvery other gate proves CONCIERGE cannot INVENT a figure, and that holds. GATE 3c\n"
            "asks the harder question: is the figure it sends an answer to the question that was\n"
            "asked? A real price attached to a misunderstood question is worse than a refusal,\n"
            "because it is signed, receipted and anchored on-chain as a commitment.\n"
            "\nThe pass condition is asymmetric on purpose: escalating, asking, or queueing for\n"
            "the owner all PASS — failing toward a human is the system working. Sending a figure\n"
            "for a question the stored profile cannot answer is the FAIL.\n"
            "\nQuestions are generated from each tenant's own profile, never written down in any\n"
            "trade's vocabulary — check 1 greps the corpus to prove it, and check 4 re-proves the\n"
            "behaviour on a tenant with no vertical template at all.\n"
        ),
    )
    try:
        from concierge import verify_phase3c
        verify_phase3c.run(r)
    except Exception as e:
        import traceback
        r.check("Phase 3c harness completed", False,
                f"The harness raised {type(e).__name__}: {e}\n"
                f"Reported as a FAIL rather than swallowed. If this is a connection error, start\n"
                f"Postgres with: docker compose up -d postgres",
                traceback.format_exc())
    return r.render()


PHASES = {
    "0": phase0, "1": phase1, "2": phase2, "3": phase3, "3c": phase3c, "4": phase4, "5": phase5, "6": phase6,
    "3b-2": phase3b2, "3b-3": phase3b3, "3b-4": phase3b4, "6b": phase6b, "8": phase8,
    "8b-1": phase8b1,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="CONCIERGE verification harness")
    ap.add_argument("--phase", type=str, required=True,
                    help="e.g. 0, 1, ... 6, or a sub-gate like 3b-2")
    args = ap.parse_args()

    key = args.phase.strip()
    if key not in PHASES:
        built = ", ".join(sorted(PHASES, key=lambda k: (len(k), k)))
        print(f"Phase {key!r} has no harness yet. Built so far: {built}.")
        print("This is not a pass. It means that phase has not been implemented.")
        return 2
    return PHASES[key]()


if __name__ == "__main__":
    sys.exit(main())
