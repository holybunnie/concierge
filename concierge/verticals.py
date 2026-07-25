"""Vertical templates (§11) — what a good profile needs, per kind of business.

The model is a manager briefing a new human sales rep: hand over what you'd hand a person,
name the gaps out loud, and show what "filled in properly" looks like.

Two rules govern everything in this file.

1. **An example is never data.** Every template carries a fully filled EXAMPLE so the tenant can
   see the shape of a good answer. That example is a different vertical's fictional business, it
   is labelled as such everywhere it is rendered, and `onboarding.build_profile` structurally
   cannot copy it into a profile — it reads only from tenant-supplied answers. The failure this
   prevents is the expensive one: a business quoting £85 because the example said £85.

2. **A gap is announced, never filled.** If the tenant has not set a cancellation policy, the
   template says so and says what it will cost them ("clients will ask; I'll escalate those").
   It does not invent a reasonable-sounding default. Inventing a default is how an agent ends up
   committing a business to terms its owner never agreed to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    """One thing the tenant must tell us, and what breaks if they don't."""

    key: str
    label: str
    question: str          # asked verbatim during onboarding
    why: str               # what this is for, in the tenant's terms
    maps_to: str           # dotted path into the profile — deterministic, no interpretation
    kind: str              # text | list | money | percent | services | duration
    required: bool
    example: object        # ILLUSTRATIVE ONLY. Never reaches a profile.
    gap_consequence: str   # what CONCIERGE will do if this is left empty
    # For money/percent fields only: the tenant's own words for what the figure is charged
    # against ("of the sale price", "per treatment"). Quoted alongside the figure so a rate is
    # never stated bare. Carries no meaning to the engine — it is text, not a branch.
    basis: str = ""


@dataclass(frozen=True)
class VerticalTemplate:
    key: str
    label: str
    example_business: str          # the fictional business the example values belong to
    typical_inquiries: tuple[str, ...]
    fields: tuple[Field, ...]
    # Things this vertical must never answer on its own, regardless of profile contents.
    hard_escalations: tuple[str, ...] = ()

    def required_fields(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.required)

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


# ---------------------------------------------------------------- shared fields

def _icp(example: str) -> Field:
    return Field(
        key="icp", label="Ideal client",
        question="Who is a good client for you, and who is a waste of your time?",
        why="Used to qualify inquiries. A poor fit is answered politely and marked, not pursued.",
        maps_to="icp", kind="text", required=True, example=example,
        gap_consequence="Without this I cannot qualify. Every inquiry gets treated as a live "
                        "lead, including the obvious time-wasters.",
    )


def _escalation(example: list[str]) -> Field:
    return Field(
        key="escalation_triggers", label="Always escalate to you",
        question="What must never be handled without you? (Anything you'd want to see personally.)",
        why="These force a hand-off to you instead of a reply, no matter what else the rules allow.",
        maps_to="escalation_triggers", kind="list", required=True, example=example,
        gap_consequence="I fall back to escalating only what the profile cannot answer. Anything "
                        "your rules technically cover, I will answer.",
    )


def _sample(example: str) -> Field:
    return Field(
        key="artifact_sample", label="A reply you've actually sent",
        question="Paste one real reply you've sent to an inquiry like this — any good one.",
        why="I copy your tone and structure from it. Without it I write in a house voice that is "
            "competent but not yours.",
        maps_to="artifact_samples", kind="text", required=True, example=example,
        gap_consequence="Replies go out in a neutral professional voice that will not sound like "
                        "you. Nothing breaks, but your clients may notice.",
    )


def _engagement_noun(example: str) -> Field:
    return Field(
        key="engagement_noun", label="What you call a booking",
        question="When someone books time with you, what do you call it? "
                 "(a viewing, a consultation, an appointment, a call-out, a session…)",
        why="This word appears in every message I send about booking. Using your trade's word "
            "instead of a generic one is the difference between sounding like your business and "
            "sounding like a web form.",
        # Advisory, not required: `lexicon.words()` falls back to a bland noun rather than
        # refusing to reply, so this degrades wording and does not block answering. Marking it
        # required would put it in the same list as "you have no prices", which would be a lie
        # about its severity — and the tenant would learn to skim that list.
        maps_to="lexicon.engagement_noun", kind="text", required=False, example=example,
        gap_consequence="Replies fall back to the word “appointment”. Nothing breaks, but if "
                        "your clients say something else, mine will sound off to them.",
    )


def _client_noun(example: str) -> Field:
    return Field(
        key="client_noun", label="What you call the people you serve",
        question="What do you call the people you serve? (clients, patients, guests, buyers…)",
        why="Used wherever a message refers to them. Getting this wrong is a small thing that "
            "reads as carelessness.",
        maps_to="lexicon.client_noun", kind="text", required=False, example=example,
        gap_consequence="Replies use the word “client”.",
    )


def _timezone() -> Field:
    return Field(
        key="timezone", label="Your timezone",
        question="What timezone do you operate in? (e.g. Europe/London)",
        why="Your availability is read in your timezone. The prospect's timezone is asked "
            "separately and never guessed.",
        maps_to="calendar_ref.timezone", kind="text", required=True, example="Europe/London",
        gap_consequence="I cannot offer slots at all. Booking is blocked until this is set.",
    )


# ---------------------------------------------------------------- the qualifier policies
#
# GATE 3c measured what clients actually ask and CONCIERGE could not answer. The four classes
# below were not chosen in the abstract — they are the qualifier classes that made up nearly
# every escalation in that sweep: how many people, how long, where, and when.
#
# Each maps onto a key `comprehension.QUALIFIER_COVERAGE` already looks for, which makes this the
# one place in the product where answering an onboarding question converts DIRECTLY into
# autonomy. Leave them blank and a client asking "can you come to us?" goes to the owner's inbox,
# forever, correctly. Fill one in and that same question is answered in the tenant's own words —
# quoted verbatim, never paraphrased, never averaged into a new figure.
#
# All four are `required=False` on purpose. A gap here is a real, working state (escalate), not a
# broken profile, and marking them required would block onboarding on questions many businesses
# genuinely have no policy for. They are advisory gaps: named out loud, with the cost stated.
#
# They are also trade-neutral, which is why they live here as shared builders rather than inside
# any one template. "Do you travel to clients?" is a question for a barrister, a mobile groomer
# and a drone surveyor alike. Only the EXAMPLE differs per template, and an example is never data.

def _group_policy(example: str) -> Field:
    return Field(
        key="group_policy", label="More than one person",
        question="If someone asks for more than one person at once — a couple, a group, a whole "
                 "team — what do you charge? (Leave blank if you have no set rule.)",
        why="'How much for two of us?' is one of the most common questions there is, and your "
            "list price answers a different question. With this I answer in your words; without "
            "it I send it to you.",
        maps_to="group_pricing", kind="text", required=False, example=example,
        gap_consequence="Any question about more than one person goes to you rather than being "
                        "answered. Nothing is guessed and no per-person price is invented.",
    )


def _travel_policy(example: str) -> Field:
    return Field(
        key="travel_policy", label="Travelling to the client",
        question="Do you go to the client, and does that change the price or the area you cover? "
                 "(Leave blank if you only work from your own place.)",
        why="'Can you come to us?' is either a yes with terms or a no, and only you know which. "
            "I will not assume either.",
        maps_to="travel_policy", kind="text", required=False, example=example,
        gap_consequence="Anyone asking you to travel to them gets passed to you instead of an "
                        "answer.",
    )


def _hours_policy(example: str) -> Field:
    return Field(
        key="hours_policy", label="Evenings, weekends and short notice",
        question="Do you work outside your normal hours — evenings, weekends, bank holidays, "
                 "urgent jobs — and does it cost more? (Leave blank if you never do.)",
        why="These arrive constantly and the honest answer is usually a rule you already have in "
            "your head. Written down once, I can give it.",
        maps_to="availability_policy", kind="text", required=False, example=example,
        gap_consequence="Every out-of-hours or urgent request escalates, even the ones you would "
                        "have happily said yes to.",
    )


def _length_policy(example: str) -> Field:
    return Field(
        key="length_policy", label="Different lengths",
        question="Do you offer the same thing at more than one length or size, at different "
                 "prices? (Leave blank if there is only one version.)",
        why="Otherwise someone asking for a longer version gets quoted the standard one, which "
            "is the wrong answer given confidently — the thing I most want to avoid.",
        maps_to="duration_options", kind="text", required=False, example=example,
        gap_consequence="A request for a longer or larger version of something goes to you.",
    )


def _qualifier_policies(group: str, travel: str, hours: str, length: str) -> tuple[Field, ...]:
    """The four shared policy questions, in the order a client is most likely to ask them."""
    return (_group_policy(group), _travel_policy(travel),
            _hours_policy(hours), _length_policy(length))


# ---------------------------------------------------------------- real estate

REAL_ESTATE = VerticalTemplate(
    key="real_estate",
    label="Estate agency / property",
    example_business="EXAMPLE ONLY — 'Northgate Property', a fictional agency",
    typical_inquiries=(
        "Is 14 Elm Road still available?",
        "Can I arrange a viewing this weekend?",
        "What's the asking price, and is there any movement on it?",
        "Do you manage rentals in this area?",
    ),
    hard_escalations=(
        "Any offer on a specific property — an offer binds a seller, not you.",
        "Anything about a chain, survey result, or completion date.",
    ),
    fields=(
        Field("property_types", "Property types",
              "What kinds of property do you handle? (sales, lettings, commercial, new build…)",
              "Determines which inquiries are yours and which get escalated as out of scope.",
              "services", "list", True,
              ["Residential sales", "Residential lettings"],
              "I cannot tell an in-scope inquiry from an out-of-scope one, so everything escalates "
              "to you and the agent is doing nothing useful."),
        Field("areas_served", "Areas served",
              "Which areas or postcodes do you cover?",
              "An inquiry about a street you don't cover is qualified out immediately.",
              "areas_served", "list", True,
              ["N1", "N5", "N16 — Islington and Stoke Newington"],
              "Out-of-area inquiries get treated as live leads and waste your time."),
        Field("price_ranges", "Typical price range",
              "What price bracket do your properties sit in?",
              "Used to qualify budget, never to quote a specific property.",
              "price_ranges", "text", True,
              "Sales £450k–£1.2m; lettings £1,400–£3,200 pcm",
              "I cannot qualify on budget. Someone looking at £200k gets the same treatment as a "
              "serious £900k buyer."),
        Field("listing_fee_pct", "Your fee",
              "What is your commission, as a percentage of sale price?",
              "This is the number I quote. It comes from here and nowhere else.",
              "pricing_rules.headline", "percent", True, 1.5,
              "I cannot quote a fee at all. Every fee question escalates to you.",
              basis="of the sale price"),
        Field("floor_pct", "Your absolute floor",
              "What is the lowest commission you will ever accept?",
              "A hard floor. If a negotiation would cross it, I stop and escalate to you — I "
              "cannot be talked below this by a persistent prospect.",
              "pricing_rules.floor", "percent", True, 1.2,
              "I will not negotiate at all. Any request for a discount escalates.",
              basis="of the sale price"),
        Field("viewing_availability", "Viewing windows",
              "When can viewings be booked? (days, hours, how much notice you need)",
              "Slots are only ever offered inside this window.",
              "calendar_ref.booking_rules", "text", True,
              "Tue–Sat 10:00–18:00, minimum 24 hours notice",
              "I cannot offer a viewing slot. Booking is blocked."),
        _timezone(),
        _engagement_noun("viewing"),
        _client_noun("buyer"),
        _icp("Buyers and landlords in Zone 2 with financing already arranged"),
        _escalation(["Any offer on a property", "Anything about an existing chain",
                     "Complaints about a previous viewing"]),
        _sample("Hi Sarah — thanks for getting in touch about 14 Elm Road. It's still available. "
                "The asking price is £625,000. I have viewings free Thursday afternoon and "
                "Saturday morning — would either suit?"),
        *_qualifier_policies(
            group="Block viewings on Saturdays for 3+ interested parties, same fee.",
            travel="I cover the whole of the postcode area; anything outside it I refer on.",
            hours="Viewings until 7pm on weekdays and Saturday mornings. No Sundays.",
            length="Standard viewings are 30 minutes; a second, longer viewing is free.",
        ),
    ),
)

# ---------------------------------------------------------------- legal

LEGAL = VerticalTemplate(
    key="legal",
    label="Barrister / solicitor / legal practice",
    example_business="EXAMPLE ONLY — 'Fenwick Chambers', a fictional set",
    typical_inquiries=(
        "Do you handle employment tribunal matters?",
        "What's your consultation fee?",
        "I'm being made redundant — can you help?",
        "Are you able to take this on before the hearing date?",
    ),
    hard_escalations=(
        "Any question that asks what the client should DO about their situation. That is legal "
        "advice. It escalates every time, without exception, regardless of profile contents.",
        "Any assessment of the merits or likely outcome of a matter.",
        "Anything touching a limitation period or filing deadline.",
    ),
    fields=(
        Field("practice_areas", "Practice areas",
              "What matters do you take on?",
              "An inquiry outside these is declined politely and escalated, never attempted.",
              "services", "list", True,
              ["Employment — unfair dismissal, discrimination", "Commercial contract disputes"],
              "I cannot tell whether a matter is yours. Everything escalates."),
        Field("jurisdictions", "Jurisdictions",
              "Which jurisdictions are you qualified in?",
              "A matter in a jurisdiction you don't cover is a conflict risk, not a lead.",
              "jurisdictions", "list", True,
              ["England & Wales"],
              "I cannot rule out matters you are not qualified to take. This is a regulatory "
              "risk, not just a commercial one."),
        Field("consultation_fee", "Consultation fee",
              "What do you charge for an initial consultation, and how is it structured?",
              "The only fee figure I will ever state.",
              "pricing_rules.headline", "money", True,
              "£250 + VAT for the first hour, fixed",
              "I cannot state a fee. Every fee question escalates to you.",
              basis="for an initial consultation"),
        Field("fee_negotiable", "Is that fee negotiable?",
              "Will you reduce the consultation fee, and if so, what is your floor?",
              "If you say no, I will not negotiate and will say so plainly.",
              "pricing_rules.floor", "money", True, "Not negotiable",
              "I treat the fee as fixed and decline all discount requests.",
              basis="for an initial consultation"),
        Field("conflict_check_rules", "Conflict checks",
              "What do you need to know before you can accept a matter? (opposing party, etc.)",
              "I collect these before booking anything, so you never take a call you must decline.",
              "conflict_check_rules", "list", True,
              ["Full name of the opposing party", "Whether proceedings have been issued"],
              "I will book consultations you may have to cancel for conflict. Avoidable and "
              "embarrassing."),
        Field("never_advise_on", "Never advise on",
              "What must I never say anything about, even in general terms?",
              "A hard block. Matched inquiries escalate to you, whatever else the profile says.",
              "never_advise_on", "list", True,
              ["Merits or likely outcome of any matter", "Limitation periods and deadlines",
               "Anything criminal"],
              "I rely only on the built-in legal hard-escalations. Those are conservative but "
              "they are generic — they don't know your practice."),
        Field("consultation_availability", "Consultation windows",
              "When can consultations be booked? (days, hours, notice required)",
              "Slots are only ever offered inside this window.",
              "calendar_ref.booking_rules", "text", True,
              "Mon–Thu 09:00–17:00, minimum 48 hours notice",
              "I cannot offer a consultation slot. Booking is blocked."),
        _timezone(),
        _engagement_noun("consultation"),
        _client_noun("client"),
        _icp("Employees and SMEs with a live employment matter, in England & Wales"),
        _escalation(["Anything urgent or with a deadline", "Any existing client",
                     "Any matter involving a current or former client of chambers"]),
        _sample("Dear Mr Patel — thank you for your enquiry. Employment matters of this kind are "
                "within my practice. I'd need to complete a short conflict check before advising "
                "further. My initial consultation is £250 + VAT for the first hour."),
        *_qualifier_policies(
            group="Multiple claimants in one matter are quoted individually — the fee depends on "
                  "whether the claims are heard together.",
            travel="Conferences in chambers or by video. I attend tribunal wherever it sits; "
                   "travel outside London is charged at cost.",
            hours="Papers are read in the evenings as a matter of course. Instructions inside 48 "
                  "hours of a hearing carry a 25% uplift.",
            length="A conference is either the standard hour or a half-day, quoted separately.",
        ),
    ),
)

# ---------------------------------------------------------------- spa / beauty

SPA_BEAUTY = VerticalTemplate(
    key="spa_beauty",
    label="Spa / salon / beauty treatments",
    example_business="EXAMPLE ONLY — 'Halcyon Rooms', a fictional spa",
    typical_inquiries=(
        "Do you do hot stone massage?",
        "How much is a facial, and how long does it take?",
        "Can I book two people for Saturday afternoon?",
        "What happens if I need to cancel?",
    ),
    hard_escalations=(
        "Any question about a medical condition, pregnancy, allergy or medication interacting "
        "with a treatment. That is a clinical judgement, not a booking question.",
    ),
    fields=(
        Field("service_menu", "Treatment menu",
              "List your treatments with duration and price — all of them.",
              "This is the price list I quote from. A treatment not on it does not exist to me.",
              "services", "services", True,
              [{"name": "Deep tissue massage", "duration_min": 60, "price": 85, "currency": "GBP"},
               {"name": "Signature facial", "duration_min": 45, "price": 70, "currency": "GBP"}],
              "I cannot quote anything. Every price question escalates to you, which for a spa "
              "is most of your inbound."),
        Field("packages", "Packages and offers",
              "Any bundles or multi-treatment prices?",
              "Quoted as-is when a client asks for more than one treatment.",
              "packages", "list", False,
              ["Half day: any two treatments + lunch, £180"],
              "I quote treatments individually. You may lose bundle upsells, nothing breaks."),
        Field("floor_price", "Your floor",
              "What is the lowest you will go on a treatment, in cash terms?",
              "A hard floor. A negotiation that would cross it stops and escalates to you.",
              "pricing_rules.floor", "money", True, 70,
              "I will not discount at all. Every haggle escalates.",
              basis="per treatment"),
        Field("max_discount_pct", "Maximum discount",
              "What is the biggest discount I may offer without asking you?",
              "Bounds negotiation from the other side — belt and braces with the floor.",
              "pricing_rules.max_discount", "percent", True, 15,
              "I hold list price and escalate any discount request."),
        Field("booking_lead_time", "Booking notice",
              "How much notice do you need for a booking?",
              "No slot inside this window is ever offered.",
              "calendar_ref.booking_rules", "text", True,
              "Minimum 24 hours; Sat bookings by Thu",
              "I cannot offer a slot. Booking is blocked."),
        Field("cancellation_policy", "Cancellation policy",
              "What is your cancellation and no-show policy?",
              "Stated at booking, so it is on the record before they arrive.",
              "cancellation_policy", "text", True,
              "48 hours' notice or 50% of the treatment price is charged",
              "Clients WILL ask this, and I will have to escalate every time — it's one of the "
              "four most common questions a spa gets. It also means no-shows arrive with nothing "
              "agreed in writing."),
        _timezone(),
        _engagement_noun("appointment"),
        _client_noun("client"),
        _icp("Local clients within a few miles, repeat bookings preferred"),
        _escalation(["Anything about pregnancy, allergies or medical conditions",
                     "Group bookings over 4 people", "Complaints about a treatment"]),
        _sample("Hi Nadia — yes, we do hot stone, it's 75 minutes at £95. I've got Saturday 2pm "
                "and Sunday 11am free this week. Shall I hold one for you?"),
        *_qualifier_policies(
            group="Two people side by side in the double room: the two treatments, plus £15 for "
                  "the room. Parties of 4+ are quoted individually.",
            travel="We don't travel — everything happens at the studio.",
            hours="Evenings until 8pm on weekdays at no extra charge. Sundays are +20%.",
            length="Most treatments come as 60 or 90 minutes; the 90 is the listed price plus £30.",
        ),
    ),
)

# ---------------------------------------------------------------- generic fallback

GENERIC = VerticalTemplate(
    key="generic",
    label="General business (no specialised template)",
    example_business="EXAMPLE ONLY — 'Meridian Consulting', a fictional consultancy",
    typical_inquiries=(
        "Do you do X?",
        "How much would that cost?",
        "Are you available in the next few weeks?",
    ),
    fields=(
        Field("services", "What you sell",
              "List what you offer, with prices.",
              "The only list I quote from. Anything not on it escalates.",
              "services", "services", True,
              [{"name": "Strategy day", "duration_min": 480, "price": 2400, "currency": "GBP"}],
              "I cannot quote anything at all."),
        Field("floor_price", "Your floor",
              "What is the lowest you will accept?",
              "A hard floor. Crossing it forces an escalation instead of a reply.",
              "pricing_rules.floor", "money", True, 1800,
              "I will not negotiate. Every discount request escalates."),
        Field("max_discount_pct", "Maximum discount",
              "What is the biggest discount I may offer without asking you? (Leave blank for none.)",
              "Bounds negotiation from the other side — belt and braces with the floor.",
              "pricing_rules.max_discount", "percent", False, 10,
              "I hold list price and escalate any discount request."),
        Field("availability", "When you're available",
              "When can meetings be booked? (days, hours, notice)",
              "Slots are only offered inside this window.",
              "calendar_ref.booking_rules", "text", True,
              "Tue–Thu 09:00–17:00, 48 hours notice",
              "Booking is blocked."),
        _timezone(),
        # The generic template carries no trade to borrow a word from, so it asks outright and
        # shows several possibilities rather than one — the tenant picks their own.
        _engagement_noun("call — or viewing, consultation, session, call-out, fitting…"),
        _client_noun("client — or patient, guest, customer, buyer…"),
        _icp("Companies of 20–200 people with a named budget holder"),
        _escalation(["Anything involving a contract or SOW", "Any existing client"]),
        _sample("Hi Tom — thanks for reaching out. A strategy day is £2,400 and runs 9–5 at your "
                "offices. I have the 14th and the 21st free."),
        *_qualifier_policies(
            group="Additional attendees beyond 8 are £120 each.",
            travel="I work on site anywhere in the UK; travel and accommodation at cost, agreed "
                   "up front.",
            hours="Weekday hours only. Weekend work is possible at +50%, agreed case by case.",
            length="A strategy day is a full day; half-days are £1,400.",
        ),
    ),
)

TEMPLATES: dict[str, VerticalTemplate] = {
    t.key: t for t in (REAL_ESTATE, LEGAL, SPA_BEAUTY, GENERIC)
}


def template_for(vertical: str) -> VerticalTemplate:
    """Unknown vertical falls back to the generic template — and never to a guess."""
    return TEMPLATES.get(vertical, GENERIC)
