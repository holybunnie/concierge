# Credits

## Platforms and services

- **OKX** — OnchainOS skills (`npx skills add okx/onchainos-skills`), Agentic Wallet, the Agent
  Payments Protocol, and X Layer. The `okx-ai` skill supplies ERC-8004 identity, the task
  marketplace, and the progress monitor; `okx-agent-payments-protocol` supplies the `a2a-pay`
  escrow path.
- **Cal.com** — open-source scheduling, API v2.
- **Postmark** — inbound email parsing and outbound delivery.

## Prior art that shaped the design

- The vertical-aware onboarding subsystem (§11) is modeled on **Piper**'s documented approach to
  briefing an AI SDR: give it what you would give a human rep, then flag the gaps it will hit.

## Standards

- **ERC-8004** — on-chain agent identity.
- **SPF / DKIM / DMARC** — sender authentication.
- **ISO 8601** — every timestamp crossing a system boundary.

## Built with

Python 3.11+, FastAPI, PostgreSQL, Docker.
