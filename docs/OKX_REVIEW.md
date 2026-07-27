# CONCIERGE — OKX reviewer handoff

The complete reviewer packet is available at:

**https://app.quietdesks.com/okx-review**

Machine-readable evidence:

**https://app.quietdesks.com/okx-review.json**

This is the preferred handoff because it requires no repository checkout, VPS login, credentials,
database setup, or sequence of local test commands.

## Identity

- Agent: **CONCIERGE #9274**
- Role: ASP
- Service: **Inbound enquiry concierge**
- Service type: A2A
- Service ID: `28086024-3013-4438-b243-3d2470fb78da`
- Network: X Layer mainnet, chain ID 196

## Completed paid proof

- Job: `0x3646b7b21028eec33742c2dba81cc0d758597e674af7696773cc906f8282a608`
- Buyer: Agent #9630
- Provider: Agent #9274
- Amount: 2.5 USDT
- Final OKX status: `complete`
- Delivered tenant inbox: `brightside-dental-2@inbox.quietdesks.com`

That single job proved provider application, escrow funding, unattended A2A onboarding, delivery
of a real dedicated inbox, buyer review approval, and escrow release.

The listing-review compatibility path separately polls every 8 seconds for a designated task from
the review buyer and applies at **the task's own posted budget**, whatever that budget is. The
reviewer's amount is not fixed — measured attempts were published at 0, 0.05 and 1 USDT — so the
route is matched on identity rather than on a price. It never applies above the posted budget,
because the platform's `reject-apply` is irreversible and a later budget raise cannot recover the
provider, and it never applies to a zero-budget task, because `onchainos agent apply` requires an
amount greater than zero. Failed or unsent application transactions alert the operator.

Measured end to end on 2026-07-27, job
`0x35ecdf02b25921dc3e0675b34c788b8ab800bee35f81623c2cc8d0345e367504`: published 16:29:42 UTC,
applied 16:30:01 (tx `0xa2f2324a2e0663e4aa8ab9c1147fde21492faec65a70b7cd5b840a4509be33ff`,
1 USDT), `job_accepted` 16:33:01, tenant provisioned 16:35:40, onboarding interview opened in the
job's own channel.

## Public endpoints

- Review packet: https://app.quietdesks.com/okx-review
- Machine-readable packet: https://app.quietdesks.com/okx-review.json
- Liveness: https://app.quietdesks.com/healthz
- Dependency readiness: https://app.quietdesks.com/readyz
- Public on-chain receipt example:
  https://app.quietdesks.com/r/ce573269-cf86-46b5-a682-6e614b48da47

`/readyz` returns HTTP 200 only when the database, outbound email configuration, authenticated
inbound path, A2A daemon, and X Layer configuration are ready.

## Shortest marketplace test

Create one private designated A2A job for Agent #9274 and the service ID above. Ask CONCIERGE to
set up inbound-enquiry handling for the buyer's own service business.

Expected behavior:

1. CONCIERGE responds in the job thread.
2. It validates the engagement price deterministically.
3. After acceptance it asks for the business's actual services, prices, floor, availability,
   escalation rules, and reply vocabulary.
4. It delivers a dedicated `@inbox.quietdesks.com` address.
5. Missing or ambiguous business facts are escalated; no price or policy is invented.

## Current publication state

All operator-controlled checks pass. OKX currently reports agent #9274 as `not listed` with
`Listing under review`; its approval remark is `AI quality review timed out, automatically
passed`. Public marketplace publication therefore remains an OKX-side transition.
