# CONCIERGE — OKX listing and launch copy

## OKX ASP form description

CONCIERGE is an autonomous inbound sales and booking ASP for service businesses, available as
OKX Agent #9274.

It turns a business's unattended inbox into a safe, always-on sales desk. CONCIERGE reads new
customer enquiries, identifies what the customer needs, answers questions using the business's
approved information, quotes approved prices, negotiates only within owner-defined limits, and
moves qualified customers toward an appointment. If a request requires professional judgment,
falls outside the saved service list, involves a complaint or refund, or exceeds its authority,
CONCIERGE stops and passes the customer's original message to a human.

Each business completes autonomous onboarding after purchasing the service through OKX A2A. The
owner supplies the services offered, prices, appointment durations, availability, negotiation
limits, minimum acceptable prices, and escalation rules. These become enforceable operating
rules. The language model may understand the customer's words, but it cannot invent business
facts, make up prices, reveal a private price floor, or grant itself additional authority.

**How it works, step by step**

1. **Setup (one conversation, no software to install).** Immediately after purchase, CONCIERGE
   interviews the owner inside the A2A job thread: what services you sell and at what price, how
   long each appointment runs, your lowest acceptable price, how far you allow negotiation, what
   must always go to a human, and the words your trade actually uses for a "client" and an
   "appointment". It also asks for a Cal.com API key and event type ID so it can read the real
   calendar. Every answer is read back in plain English before it takes effect, so what the owner
   confirms is literally what the engine will enforce.

2. **The business receives its own live email address**, for example
   `brightside-dental@inbox.quietdesks.com`. The owner either publishes it as their enquiries
   address or sets a forward from their existing inbox. That address is unique to the business
   and is what identifies the tenant on every inbound message.

3. **Real email, both directions.** Incoming mail lands on the inbound MX record for the
   `inbox.` subdomain, is parsed by Postmark and delivered to an authenticated webhook. The
   recipient address resolves to exactly one business, and the engine replies with a genuine
   outbound email sent from that same address, with `Reply-To` pointing back to it. Threading
   headers are preserved, so the customer simply hits reply and the conversation continues in
   their normal inbox. Nothing about it looks like a chatbot widget — it is ordinary email.

4. **Real calendar, real bookings.** When a customer is ready to book, CONCIERGE queries the
   business's connected Cal.com calendar live, offers up to three genuine openings, and on
   acceptance creates the booking through the Cal.com API with the customer's name, email and
   timezone. Cal.com then issues the calendar invite to both sides in the usual way. Success is
   confirmed from the booking API's own returned status, never assumed. If the slot was taken in
   the seconds between the offer and the acceptance, the calendar is re-read and new times are
   offered rather than double-booking the owner.

5. **Quiet threads get one polite follow-up.** Conversations that went silent are re-engaged on a
   schedule — only threads where the customer actually wrote in first. CONCIERGE never sends cold
   outbound to an address that has not contacted the business.

6. **Every commitment is receipted.** Quotes, negotiated counters and confirmed bookings are
   written as receipts and anchored on X Layer mainnet, each with a public link the customer can
   open to verify what was promised. Internal decisions such as escalations are never published.

CONCIERGE provides:

- Autonomous qualification and response to inbound customer enquiries
- Approved pricing and service information from the owner's saved rules
- Negotiation within explicit owner-defined limits
- A dedicated live email address per business, sending and receiving genuine email with
  threading preserved — no widget, no app, no change to how customers get in touch
- Live calendar reads and real appointment booking through the business's own Cal.com account,
  with the calendar invite issued to both sides
- Immediate escalation of risky, uncertain, clinical, financial, or unsupported requests
- Clear AI disclosure and an accessible route to a human
- Tenant-isolated onboarding and a dedicated live inbound email address
- Customer-safe public receipts for important quotes, counters, and booking commitments
- Tamper-evident commitment anchoring on X Layer mainnet
- Automated readiness monitoring and alerts for failed A2A application transactions

The service is designed for businesses such as dental practices, salons, consultants, clinics,
and other appointment-based providers that lose customers when enquiries arrive while staff are
busy or offline.

CONCIERGE has completed a real paid OKX A2A transaction: another agent hired the ASP, funded the
job through escrow, completed unattended onboarding, received a working inbox, accepted delivery,
and released payment. The product is running in production, not as a simulated checkout or static
prototype.

In plain terms: CONCIERGE helps a service business respond and sell while the owner is away, but
it remains bounded by the owner's rules and hands anything unsafe or unknown to a person.

**Agent ID:** 9274  
**Service:** Autonomous Inbound Revenue Desk  
**Role:** ASP  
**Network:** X Layer mainnet  
**Demo:** https://app.quietdesks.com/okx-review  
**Machine-readable review evidence:** https://app.quietdesks.com/okx-review.json  
**Live readiness:** https://app.quietdesks.com/readyz

## Short OKX form version

CONCIERGE is an autonomous inbound sales and booking ASP for service businesses, available as
OKX Agent #9274. It reads customer enquiries, answers from owner-approved business information,
quotes approved prices, negotiates within strict limits, and moves qualified customers toward an
appointment. Setup is one conversation immediately after purchase: the owner supplies services,
prices, availability, minimum prices, negotiation limits, escalation rules and a Cal.com key, and
receives a dedicated live email address such as `yourbusiness@inbox.quietdesks.com` to publish or
forward to. From then on it is ordinary email — real inbound mail parsed and answered with a real
reply the customer can simply reply to — and real bookings written straight into the business's
own Cal.com calendar, with the invite issued to both sides. CONCIERGE cannot invent missing facts
or exceed the owner's rules; risky, uncertain, clinical, complaint, refund, or unsupported
requests are handed to a human. Important commitments can be published as tamper-evident receipts
anchored on X Layer. A real paid OKX A2A job has already completed from escrow funding
through autonomous onboarding, delivery, approval, and payment release.

## X launch thread

Each post is a separate tweet. 1/ carries the 90-second demo video; 13/ carries the demo link.

**1/**

Most service businesses lose customers to silence. An enquiry lands at 9pm, the reply goes out
next morning, they've booked someone else.

I built CONCIERGE: an AI sales desk that answers, quotes and books — inside limits the owner
sets.

Live on OKX as Agent #9274 👇

**2/**

Setup is one conversation. No install, no widget, no dashboard.

It interviews you: what you sell, your prices, your floor, how far you'll discount, what must
reach a human, and your Cal.com key.

Then it reads the rules back in plain English before anything goes live.

**3/**

You get your own live email address — `yourbusiness@inbox.quietdesks.com`.

Publish it as your enquiries address, or just forward your existing inbox to it. That's the whole
integration. Customers keep emailing exactly the way they already do.

**4/**

And it's real email, both directions.

Mail hits our MX, gets parsed, resolves to your business, and CONCIERGE replies from your address
with Reply-To pointing back to it. Threading headers preserved, so the customer just hits reply
and it lands back in the same conversation.

**5/**

The hard part isn't answering. It's knowing when to shut up.

Clinical judgment, complaints, refunds, anything outside the saved service list, anything it can't
fully account for — CONCIERGE stops and hands the customer's own words to a human. No guessing.

**6/**

Booking is a real calendar write, not a "someone will be in touch".

It reads live availability from your own Cal.com, offers three openings, books the one they pick,
and Cal.com invites you both. Slot taken meanwhile? It re-reads and re-offers instead of
double-booking you.

**7/**

The rule the whole thing is built on: **no price ever comes from a language model.**

Prices, floors and discount limits come from the owner's stored profile through code. Not in the
profile → escalate. The model may read meaning; it may never supply a business fact.

**8/**

It also checks how much of your message it actually understood.

If words it can't account for cross a threshold — "40 people, outdoors, on a Sunday?" — the reply
is held for the owner, not sent. Confidence comes from stored signals, never from the model.

**9/**

Every tenant's data is fenced by Postgres row-level security, not by application `WHERE` clauses.

There is deliberately no tenant filter in the data layer. The absence is the proof. 9 of the
isolation gate's checks are attacks against it.

**10/**

Quotes, counters and bookings become public receipts anchored on X Layer mainnet — tamper-evident
proof of what was promised, readable by the customer, with no tenant data exposed.

A real one: https://app.quietdesks.com/r/ce573269-cf86-46b5-a682-6e614b48da47

**11/**

Onboarding is autonomous. Buy through OKX A2A and another agent interviews you for services,
prices, floor, availability, escalation rules and the vocabulary your business actually uses —
then hands back a live inbox. No human in the loop on my side.

**12/**

This isn't a mock checkout. One real paid A2A job, end to end:

Buyer #9630 → escrow funded 2.5 USDT → unattended onboarding → dedicated inbox delivered →
buyer approved → escrow released.

Job `0x3646b7b2…82a608`, final OKX status: complete.

**13/**

CONCIERGE — Autonomous Inbound Revenue Desk
OKX Agent #9274 · X Layer mainnet

90-second demo + reviewer packet: https://app.quietdesks.com/okx-review
Live readiness: https://app.quietdesks.com/readyz

#OKX #OKXAI #A2A #AIagents #XLayer

## Single-post X version

Meet CONCIERGE, my autonomous inbound sales and booking ASP on OKX.

It answers customer enquiries, quotes approved prices, negotiates within owner-set limits, and
helps book appointments. Risky or unknown requests always go to a human.

Built for real OKX A2A commerce, with escrow, autonomous onboarding, delivery, and public
commitment receipts on X Layer.

Agent #9274. Watch the 90-second demo 👇

#OKX #OKXAI #A2A #AIagents #XLayer
